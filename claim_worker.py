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

        await page.wait_for_timeout(1500)

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
        await name_input.fill(full_name)

        await page.wait_for_timeout(250)

        await email_input.click()
        await email_input.fill(email)

        await page.wait_for_timeout(250)

        await whatsapp_input.click()
        await whatsapp_input.fill(whatsapp)

        try:
            await whatsapp_input.press("Tab")
        except Exception:
            pass

        await page.wait_for_timeout(1000)

        if not await click_lanjut(page):
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
            if await confirmation_page_detected(page):
                session.state = "WAITING_OTP"
                return session

            await asyncio.sleep(0.5)

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
    # XL code = 6 karakter huruf/angka.
    # Contoh: PHSVAR / YONCOS / CKBILA
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

    await page.wait_for_timeout(800)

    inputs = page.locator(
        "input:visible"
    )

    count = await inputs.count()

    if count < 6:
        raise RuntimeError(
            f"CODE_INPUT_FAIL: hanya menemukan {count} kotak kode."
        )

    # ========================================================
    # 1 KOTAK = 1 KARAKTER
    #
    # PHSVAR:
    # 1=P
    # 2=H
    # 3=S
    # 4=V
    # 5=A
    # 6=R
    #
    # Klik manual satu-satu dan pakai keyboard event.
    # ========================================================

    for index in range(6):
        char = code[index]

        box = inputs.nth(index)

        await box.scroll_into_view_if_needed()

        # Klik box spesifik
        await box.click()

        await page.wait_for_timeout(150)

        # Bersihkan value lama
        try:
            await box.press("Control+A")
            await box.press("Backspace")
        except Exception:
            try:
                await box.fill("")
            except Exception:
                pass

        await page.wait_for_timeout(100)

        # Ketik lewat keyboard event
        await page.keyboard.type(
            char,
            delay=120
        )

        await page.wait_for_timeout(300)

        # Verifikasi value benar-benar masuk
        try:
            value = await box.input_value()

            if value.upper() != char:
                raise RuntimeError(
                    f"Karakter ke-{index + 1} gagal masuk. "
                    f"Expected={char}, Actual={value}"
                )
        except Exception as exc:
            if "Expected=" in str(exc):
                raise

    # Trigger blur/change validation pada karakter terakhir
    try:
        await inputs.nth(5).press(
            "Tab"
        )
    except Exception:
        pass

    # Tunggu state React/form berubah
    await page.wait_for_timeout(
        1800
    )

    # ========================================================
    # CARI DAN KLIK LANJUT SETELAH KODE
    # ========================================================

    lanjut_clicked = await click_lanjut(
        page
    )

    if not lanjut_clicked:
        session.state = "WAITING_OTP"

        return (
            "Kode sudah masuk ke 6 kotak, "
            "tetapi tombol Lanjut masih belum aktif."
        )

    # Tunggu halaman berikutnya
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
    # CEK HASIL
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

    if await confirmation_page_detected(page):
        session.state = "WAITING_OTP"

        return (
            "Kode sudah diisi dan tombol Lanjut sudah diklik, "
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
