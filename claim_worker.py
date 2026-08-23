import asyncio
import os
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


async def click_first_lanjut(page):
    candidates = [
        page.get_by_role(
            "button",
            name=re.compile(
                r"^\s*lanjut\s*$",
                re.I
            )
        ),
        page.locator(
            "button"
        ).filter(
            has_text=re.compile(
                r"^\s*lanjut\s*$",
                re.I
            )
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

                for _ in range(40):
                    if await button.is_enabled():
                        await button.click()
                        return True

                    await page.wait_for_timeout(250)

        except Exception:
            continue

    return False


async def confirmation_page_detected(page):
    try:
        body = (
            await page.locator("body").inner_text()
        ).lower()

        markers = [
            "kode konfirmasi",
            "kirim ulang kode",
            "kode verifikasi",
            "verification code",
        ]

        if any(marker in body for marker in markers):
            return True

    except Exception:
        pass

    return False


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

        inputs = await get_visible_inputs(
            page
        )

        if len(inputs) < 3:
            raise RuntimeError(
                f"FORM_FAIL: hanya menemukan {len(inputs)} input."
            )

        name_input = inputs[0]
        email_input = inputs[1]
        whatsapp_input = inputs[2]

        await name_input.click()
        await name_input.fill(
            full_name
        )

        await page.wait_for_timeout(
            300
        )

        await email_input.click()
        await email_input.fill(
            email
        )

        await page.wait_for_timeout(
            300
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

        if not await click_first_lanjut(
            page
        ):
            raise RuntimeError(
                "BUTTON_FAIL: tombol Lanjut awal tidak dapat diklik."
            )

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
    # Kode XL berupa 6 karakter huruf/angka.
    # Contoh: YONCOS, CKBILA, ABC123.
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
    # Ambil enam kotak kode
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
    # Isi 1 karakter per kotak
    #
    # YONCOS:
    # 1 = Y
    # 2 = O
    # 3 = N
    # 4 = C
    # 5 = O
    # 6 = S
    # ========================================================

    for index in range(6):
        char = code[index]

        box = inputs.nth(
            index
        )

        await box.scroll_into_view_if_needed()

        await box.click()

        await page.wait_for_timeout(
            120
        )

        try:
            await box.fill("")
        except Exception:
            try:
                await box.press(
                    "Control+A"
                )
                await box.press(
                    "Backspace"
                )
            except Exception:
                pass

        await page.wait_for_timeout(
            80
        )

        # Ketik satu karakter saja.
        await box.type(
            char,
            delay=120
        )

        await page.wait_for_timeout(
            300
        )

    # ========================================================
    # Tunggu frontend memvalidasi 6 karakter
    # ========================================================

    await page.wait_for_timeout(
        1500
    )

    # ========================================================
    # Cari tombol Lanjut PALING BAWAH
    # ========================================================

    lanjut_candidates = [
        page.get_by_role(
            "button",
            name=re.compile(
                r"^\s*lanjut\s*$",
                re.I
            )
        ),
        page.locator(
            "button"
        ).filter(
            has_text=re.compile(
                r"^\s*lanjut\s*$",
                re.I
            )
        ),
        page.locator(
            "button[type='submit']"
        ),
    ]

    lanjut_button = None

    for locator in lanjut_candidates:
        try:
            count = await locator.count()

            for i in range(count):
                button = locator.nth(i)

                if await button.is_visible():
                    lanjut_button = button
                    break

            if lanjut_button is not None:
                break

        except Exception:
            pass

    if lanjut_button is None:
        session.state = "WAITING_OTP"

        return (
            "Kode sudah masuk ke 6 kotak, "
            "tetapi tombol Lanjut tidak ditemukan."
        )

    # ========================================================
    # Scroll ke tombol Lanjut
    # ========================================================

    await lanjut_button.scroll_into_view_if_needed()

    await page.wait_for_timeout(
        500
    )

    # ========================================================
    # Tunggu tombol yang tadinya abu-abu menjadi aktif
    # ========================================================

    enabled = False

    for _ in range(50):
        try:
            if (
                await lanjut_button.is_visible()
                and
                await lanjut_button.is_enabled()
            ):
                enabled = True
                break

        except Exception:
            pass

        await page.wait_for_timeout(
            250
        )

    if not enabled:
        session.state = "WAITING_OTP"

        return (
            "Kode sudah masuk ke 6 kotak, "
            "tetapi tombol Lanjut masih belum aktif."
        )

    # ========================================================
    # Klik Lanjut setelah kode
    # ========================================================

    await lanjut_button.click()

    # ========================================================
    # Tunggu perpindahan halaman
    # ========================================================

    try:
        await page.wait_for_load_state(
            "networkidle",
            timeout=10000
        )
    except Exception:
        pass

    await page.wait_for_timeout(
        4000
    )

    # ========================================================
    # Cek error kode
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
    # Kalau masih di halaman kode, berarti belum lanjut
    # ========================================================

    if await confirmation_page_detected(
        page
    ):
        session.state = "WAITING_OTP"

        return (
            "Kode sudah dimasukkan dan tombol Lanjut sudah diklik, "
            "tetapi halaman XL masih berada di tahap konfirmasi."
        )

    session.state = "DONE"

    return (
        "Kode konfirmasi berhasil dan proses XL dilanjutkan."
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
