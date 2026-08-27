
import os
import random
import re
from dataclasses import dataclass

from playwright.async_api import async_playwright


CLAIM_URL = os.getenv(
    "CLAIM_URL",
    "https://www.xl.co.id/esim-trial/claim"
)

HEADLESS = (
    os.getenv("HEADLESS", "true").lower()
    not in {"false", "0", "no"}
)

TIMEOUT = int(
    os.getenv(
        "BROWSER_TIMEOUT_MS",
        "45000"
    )
)


@dataclass
class ClaimSession:
    pw: object
    browser: object
    context: object
    page: object
    state: str = "FORM"

    async def close(self):
        try:
            await self.context.close()
        except Exception:
            pass

        try:
            await self.browser.close()
        except Exception:
            pass

        try:
            await self.pw.stop()
        except Exception:
            pass


async def get_visible_inputs(page):
    locator = page.locator("input:visible")
    count = await locator.count()

    return [
        locator.nth(i)
        for i in range(count)
    ]


async def _first_visible(locator):
    """Return locator pertama yang benar-benar visible, atau None."""
    try:
        count = await locator.count()
    except Exception:
        return None

    for i in range(count):
        item = locator.nth(i)
        try:
            if await item.is_visible():
                return item
        except Exception:
            continue

    return None


async def _field_by_hints(scope, hints):
    """Cari field berdasarkan placeholder/name/aria-label, bukan urutan DOM."""
    for hint in hints:
        pattern = re.compile(hint, re.I)
        candidates = [
            scope.get_by_placeholder(pattern),
            scope.get_by_label(pattern),
        ]

        # Selector atribut eksplisit lebih stabil untuk React/Next forms.
        attr_candidates = [
            scope.locator(f'input[placeholder*="{hint}" i]'),
            scope.locator(f'input[name*="{hint}" i]'),
            scope.locator(f'input[aria-label*="{hint}" i]'),
        ]

        for locator in candidates + attr_candidates:
            item = await _first_visible(locator)
            if item is not None:
                return item

    return None


async def find_initial_form_fields(page):
    """
    XL sempat mengubah form sehingga mengandalkan input[0..2] tidak stabil.
    Cari Nama/Email/WhatsApp berdasarkan semantic hints dan cek semua frame.
    """
    scopes = [page] + [frame for frame in page.frames if frame != page.main_frame]

    for scope in scopes:
        name_input = await _field_by_hints(
            scope, ["Nama Lengkap", "nama lengkap", "full name", "nama"]
        )
        email_input = await _field_by_hints(
            scope, ["Email", "email"]
        )
        whatsapp_input = await _field_by_hints(
            scope, ["Nomor WhatsApp", "WhatsApp", "whatsapp", "nomor"]
        )

        if name_input is not None and email_input is not None and whatsapp_input is not None:
            return name_input, email_input, whatsapp_input

    # Fallback terakhir: ambil visible text-like inputs dan abaikan hidden/radio/checkbox.
    for scope in scopes:
        locator = scope.locator(
            "input:visible:not([type='hidden']):not([type='radio']):not([type='checkbox']):not([type='submit'])"
        )
        try:
            count = await locator.count()
        except Exception:
            count = 0

        if count >= 3:
            return locator.nth(0), locator.nth(1), locator.nth(2)

    # Diagnostic agar error Telegram jauh lebih berguna saat XL ubah UI lagi.
    diagnostics = []
    for idx, scope in enumerate(scopes):
        try:
            all_inputs = scope.locator("input")
            total = await all_inputs.count()
            visible = 0
            attrs = []
            for i in range(min(total, 12)):
                item = all_inputs.nth(i)
                try:
                    if await item.is_visible():
                        visible += 1
                    attrs.append({
                        "type": await item.get_attribute("type"),
                        "name": await item.get_attribute("name"),
                        "placeholder": await item.get_attribute("placeholder"),
                        "aria": await item.get_attribute("aria-label"),
                    })
                except Exception:
                    pass
            diagnostics.append(f"scope={idx} total={total} visible={visible} attrs={attrs}")
        except Exception:
            pass

    raise RuntimeError(
        "FORM_FAIL: field Nama Lengkap / Email / Nomor WhatsApp tidak terdeteksi. "
        + " | ".join(diagnostics)
    )


async def click_lanjut(page):
    candidates = [
        page.get_by_role(
            "button",
            name=re.compile(r"^\s*lanjut\s*$", re.I)
        ),
        page.locator("button").filter(
            has_text=re.compile(r"^\s*lanjut\s*$", re.I)
        ),
        page.get_by_text(
            "Lanjut",
            exact=True
        ),
        page.locator(
            "button[type='submit']"
        ),
    ]

    for locator in candidates:
        try:
            count = await locator.count()

            for i in range(count):
                button = locator.nth(i)

                if not await button.is_visible():
                    continue

                await button.scroll_into_view_if_needed()

                for _ in range(50):
                    try:
                        if await button.is_enabled():
                            await button.click()
                            return True
                    except Exception:
                        pass

                    await page.wait_for_timeout(250)

        except Exception:
            continue

    return False


async def confirmation_page_detected(page):
    try:
        text = (
            await page.locator("body").inner_text()
        ).lower()

        return any(
            marker in text
            for marker in [
                "kode konfirmasi",
                "kirim ulang kode",
                "kode verifikasi",
                "verification code",
            ]
        )

    except Exception:
        return False


async def number_page_detected(page):
    try:
        text = (
            await page.locator("body").inner_text()
        ).lower()

        return (
            "pilih nomor esim" in text
            or "nomor rekomendasi" in text
        )

    except Exception:
        return False


async def select_random_esim_number(page):
    # Tunggu halaman pilihan nomor
    deadline = (
        asyncio.get_running_loop().time()
        + 20
    )

    while (
        asyncio.get_running_loop().time()
        < deadline
    ):
        if await number_page_detected(page):
            break

        await asyncio.sleep(0.5)

    if not await number_page_detected(page):
        raise RuntimeError(
            "Halaman Pilih Nomor eSIM tidak terdeteksi."
        )

    await page.wait_for_timeout(1000)

    # ========================================================
    # CARA 1 — RADIO BUTTON
    # ========================================================

    radios = page.locator(
        "input[type='radio']:visible"
    )

    radio_count = await radios.count()

    if radio_count > 0:
        random_index = random.randrange(
            radio_count
        )

        radio = radios.nth(
            random_index
        )

        await radio.scroll_into_view_if_needed()
        await radio.click()

        await page.wait_for_timeout(
            700
        )

    else:
        # ====================================================
        # CARA 2 — PILIH CARD BERDASARKAN NOMOR HP
        #
        # Contoh:
        # 0818 2800 710
        # 0819 9564 1757
        # ====================================================

        body = await page.locator(
            "body"
        ).inner_text()

        numbers = re.findall(
            r"\b08\d(?:[\s-]?\d){7,11}\b",
            body
        )

        # Hilangkan duplicate
        unique_numbers = []

        for number in numbers:
            cleaned = re.sub(
                r"\s+",
                " ",
                number
            ).strip()

            if cleaned not in unique_numbers:
                unique_numbers.append(
                    cleaned
                )

        if not unique_numbers:
            raise RuntimeError(
                "Tidak menemukan nomor rekomendasi eSIM."
            )

        chosen_number = random.choice(
            unique_numbers
        )

        number_text = page.get_by_text(
            chosen_number,
            exact=True
        )

        if await number_text.count() == 0:
            raise RuntimeError(
                f"Nomor {chosen_number} ditemukan "
                "tetapi card tidak dapat diklik."
            )

        element = number_text.first

        await element.scroll_into_view_if_needed()

        # Coba klik teks
        try:
            await element.click()
        except Exception:
            # Coba parent card
            parent = element.locator(
                "xpath=.."
            )

            await parent.click()

        await page.wait_for_timeout(
            700
        )

    # ========================================================
    # KLIK LANJUT SETELAH PILIH NOMOR
    # ========================================================

    if not await click_lanjut(page):
        raise RuntimeError(
            "Nomor eSIM sudah dipilih, "
            "tetapi tombol Lanjut tidak dapat diklik."
        )

    await page.wait_for_timeout(
        3500
    )

    return True


async def start_claim(
    full_name,
    email,
    whatsapp
):
    pw = await async_playwright().start()

    browser = await pw.chromium.launch(
        headless=HEADLESS
    )

    context = await browser.new_context(
        locale="id-ID",
        viewport={
            "width": 1365,
            "height": 960
        }
    )

    page = await context.new_page()

    page.set_default_timeout(
        TIMEOUT
    )

    session = ClaimSession(
        pw=pw,
        browser=browser,
        context=context,
        page=page
    )

    try:
        # ====================================================
        # OPEN XL
        # ====================================================

        await page.goto(
            CLAIM_URL,
            wait_until="domcontentloaded"
        )

        try:
            await page.wait_for_load_state(
                "networkidle",
                timeout=15000
            )
        except Exception:
            pass

        await page.wait_for_timeout(
            1500
        )

        # ====================================================
        # FORM AWAL
        #
        # Jangan lagi mengandalkan urutan input[0..2]. XL dapat
        # mengubah wrapper/DOM tanpa mengubah tampilan form.
        # Screenshot terbaru tetap berisi:
        # - Nama Lengkap
        # - Email
        # - Nomor WhatsApp
        # ====================================================

        try:
            await page.get_by_text(
                re.compile(r"Mulai\s+Isi\s+Data", re.I)
            ).first.wait_for(state="visible", timeout=20000)
        except Exception:
            # Heading bukan syarat mutlak; lanjutkan ke pencarian field.
            pass

        name_input, email_input, whatsapp_input = await find_initial_form_fields(page)

        await name_input.click()
        await name_input.fill(
            full_name
        )

        await page.wait_for_timeout(
            250
        )

        await email_input.click()
        await email_input.fill(
            email
        )

        await page.wait_for_timeout(
            250
        )

        await whatsapp_input.click()
        await whatsapp_input.fill(
            whatsapp
        )

        try:
            await whatsapp_input.press(
                "Tab"
            )
        except Exception:
            pass

        await page.wait_for_timeout(
            1000
        )

        if not await click_lanjut(
            page
        ):
            raise RuntimeError(
                "BUTTON_FAIL: tombol Lanjut awal tidak dapat diklik."
            )

        # ====================================================
        # WAIT FOR CODE PAGE
        # ====================================================

        deadline = (
            asyncio.get_running_loop().time()
            + 45
        )

        while (
            asyncio.get_running_loop().time()
            < deadline
        ):
            if await confirmation_page_detected(
                page
            ):
                session.state = "WAITING_OTP"
                return session

            await asyncio.sleep(
                0.5
            )

        raise RuntimeError(
            "CODE_STAGE_FAIL: halaman Kode Konfirmasi tidak terdeteksi."
        )

    except Exception:
        await session.close()
        raise


async def submit_otp(
    session,
    code
):
    # ========================================================
    # KODE XL = 6 KARAKTER
    #
    # Contoh:
    #
    # TXVHPU
    # YONCOS
    # CKBILA
    # ========================================================

    code = re.sub(
        r"[^A-Za-z0-9]",
        "",
        code
    ).upper()

    if not re.fullmatch(
        r"[A-Z0-9]{6}",
        code
    ):
        raise ValueError(
            "Kode konfirmasi harus tepat 6 karakter huruf/angka."
        )

    page = session.page

    await page.wait_for_timeout(
        800
    )

    # ========================================================
    # AMBIL 6 KOTAK KODE
    # ========================================================

    inputs = page.locator(
        "input:visible"
    )

    count = await inputs.count()

    if count < 6:
        raise RuntimeError(
            f"CODE_INPUT_FAIL: hanya menemukan {count} kotak kode."
        )

    # ========================================================
    # BERSIHKAN KOTAK
    # ========================================================

    for i in range(6):
        try:
            await inputs.nth(i).fill("")
        except Exception:
            pass

    await page.wait_for_timeout(
        300
    )

    # ========================================================
    # XL AUTO-FOCUS
    #
    # Klik kotak pertama sekali,
    # lalu ketik karakter satu per satu.
    # ========================================================

    first_box = inputs.nth(
        0
    )

    await first_box.scroll_into_view_if_needed()
    await first_box.click()

    await page.wait_for_timeout(
        300
    )

    for char in code:
        await page.keyboard.type(
            char,
            delay=180
        )

        await page.wait_for_timeout(
            250
        )

    # ========================================================
    # VALIDASI ISI
    # ========================================================

    await page.wait_for_timeout(
        1000
    )

    values = []

    for i in range(6):
        try:
            value = (
                await inputs
                .nth(i)
                .input_value()
            ).upper()

            values.append(
                value
            )

        except Exception:
            values.append("")

    actual_code = "".join(
        values
    )

    if actual_code != code:
        raise RuntimeError(
            "Kode tidak terisi sempurna. "
            f"Expected={code}, Actual={actual_code}"
        )

    # Trigger validation
    try:
        await inputs.nth(5).press(
            "Tab"
        )
    except Exception:
        pass

    await page.wait_for_timeout(
        1500
    )

    # ========================================================
    # KLIK LANJUT SETELAH KODE
    # ========================================================

    if not await click_lanjut(
        page
    ):
        session.state = "WAITING_OTP"

        return (
            "Kode sudah terisi ke 6 kotak, "
            "tetapi tombol Lanjut masih belum aktif."
        )

    # ========================================================
    # WAIT PILIH NOMOR eSIM
    # ========================================================

    await page.wait_for_timeout(
        2500
    )

    # ========================================================
    # CEK KODE DITOLAK
    # ========================================================

    try:
        body_text = (
            await page.locator("body").inner_text()
        ).lower()
    except Exception:
        body_text = ""

    error_markers = [
        "kode salah",
        "kode tidak valid",
        "invalid code",
        "expired",
        "kedaluwarsa",
        "kadaluarsa",
    ]

    if any(
        marker in body_text
        for marker in error_markers
    ):
        session.state = "WAITING_OTP"

        return (
            "Kode ditolak oleh XL. "
            "Silakan masukkan kode terbaru."
        )

    # ========================================================
    # PILIH NOMOR RANDOM
    # ========================================================

    try:
        await select_random_esim_number(
            page
        )

    except Exception as exc:
        raise RuntimeError(
            f"Gagal memilih nomor eSIM: {exc}"
        )

    session.state = "NUMBER_SELECTED"

    return (
        "Kode konfirmasi berhasil. "
        "Nomor eSIM random sudah dipilih "
        "dan tombol Lanjut sudah diklik."
    )


async def resend_code(
    session
):
    page = session.page

    try:
        locator = page.get_by_text(
            "Kirim Ulang Kode",
            exact=False
        )

        for i in range(
            await locator.count()
        ):
            item = locator.nth(i)

            if await item.is_visible():
                await item.click()

                await page.wait_for_timeout(
                    1500
                )

                session.state = "WAITING_OTP"

                return (
                    "Kode baru sudah diminta. "
                    "Silakan cek email."
                )

    except Exception:
        pass

    raise RuntimeError(
        "Tombol Kirim Ulang Kode tidak ditemukan."
    )
