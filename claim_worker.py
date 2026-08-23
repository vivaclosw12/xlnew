import asyncio
import os
import re
from dataclasses import dataclass

from playwright.async_api import async_playwright


# ============================================================
# CONFIG
# ============================================================

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


# ============================================================
# SESSION
# ============================================================

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


# ============================================================
# HELPERS
# ============================================================

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
        page.locator(
            "button"
        ).filter(
            has_text=re.compile(r"^\s*lanjut\s*$", re.I)
        ),
        page.locator(
            "button[type='submit']"
        ),
    ]

    for locator in candidates:
        try:
            for i in range(await locator.count()):
                button = locator.nth(i)

                if not await button.is_visible():
                    continue

                await button.scroll_into_view_if_needed()

                for _ in range(30):
                    if await button.is_enabled():
                        await button.click()
                        return True

                    await page.wait_for_timeout(200)

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


# ============================================================
# START CLAIM
# ============================================================

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

        # Halaman awal XL:
        # input 0 = Nama Lengkap
        # input 1 = Email
        # input 2 = Nomor WhatsApp

        inputs = await get_visible_inputs(page)

        if len(inputs) < 3:
            raise RuntimeError(
                f"FORM_FAIL: hanya menemukan {len(inputs)} input."
            )

        name_input = inputs[0]
        email_input = inputs[1]
        whatsapp_input = inputs[2]

        # Nama
        await name_input.click()
        await name_input.fill(full_name)

        await page.wait_for_timeout(300)

        # Email
        await email_input.click()
        await email_input.fill(email)

        await page.wait_for_timeout(300)

        # WhatsApp
        await whatsapp_input.click()
        await whatsapp_input.fill(whatsapp)

        await page.wait_for_timeout(300)

        # Trigger validation
        try:
            await whatsapp_input.press("Tab")
        except Exception:
            pass

        await page.wait_for_timeout(1000)

        if not await click_lanjut(page):
            raise RuntimeError(
                "BUTTON_FAIL: tombol Lanjut tidak dapat diklik."
            )

        # Tunggu halaman kode konfirmasi
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


# ============================================================
# SUBMIT CONFIRMATION CODE
# ============================================================

async def submit_otp(
    session,
    code
):
    # --------------------------------------------------------
    # XL confirmation code can contain LETTERS.
    #
    # Examples:
    # CKBILA
    # YKZVAT
    # JYBFTI
    #
    # DO NOT use \d{6}
    # DO NOT remove letters.
    # --------------------------------------------------------

    code = re.sub(
        r"[^A-Za-z0-9]",
        "",
        code
    ).upper()

    # EXACTLY 6 alphanumeric characters
    if not re.fullmatch(
        r"[A-Z0-9]{6}",
        code
    ):
        raise ValueError(
            "Kode konfirmasi harus tepat 6 karakter huruf/angka."
        )

    page = session.page

    await page.wait_for_timeout(1000)

    # ========================================================
    # GET SIX CODE BOXES
    # ========================================================

    inputs = page.locator("input:visible")
    count = await inputs.count()

    if count < 6:
        raise RuntimeError(
            f"CODE_INPUT_FAIL: hanya menemukan {count} kotak kode."
        )

    # ========================================================
    # 1 BOX = 1 CHARACTER
    #
    # Example CKBILA:
    #
    # BOX 1 = C
    # BOX 2 = K
    # BOX 3 = B
    # BOX 4 = I
    # BOX 5 = L
    # BOX 6 = A
    #
    # Explicit click every box.
    # ========================================================

    for index in range(6):
        char = code[index]
        box = inputs.nth(index)

        await box.scroll_into_view_if_needed()

        # Klik field satu-satu
        await box.click()

        await page.wait_for_timeout(150)

        # Bersihkan hanya field ini
        try:
            await box.fill("")
        except Exception:
            try:
                await box.press("Control+A")
                await box.press("Backspace")
            except Exception:
                pass

        await page.wait_for_timeout(100)

        # Ketik SATU karakter
        await box.type(
            char,
            delay=120
        )

        # Tunggu sebelum klik kotak berikutnya
        await page.wait_for_timeout(350)

    # Tunggu frontend membaca 6 karakter
    await page.wait_for_timeout(1000)

    # ========================================================
    # CLICK CONFIRM / VERIFY IF AVAILABLE
    # ========================================================

    button_clicked = False

    try:
        buttons = page.get_by_role(
            "button",
            name=re.compile(
                r"konfirmasi|verifikasi|lanjut",
                re.I
            )
        )

        for i in range(await buttons.count()):
            button = buttons.nth(i)

            if (
                await button.is_visible()
                and await button.is_enabled()
            ):
                await button.click()
                button_clicked = True
                break

    except Exception:
        pass

    if not button_clicked:
        try:
            submit_buttons = page.locator(
                "button[type='submit']:visible"
            )

            for i in range(await submit_buttons.count()):
                button = submit_buttons.nth(i)

                if (
                    await button.is_visible()
                    and await button.is_enabled()
                ):
                    await button.click()
                    button_clicked = True
                    break

        except Exception:
            pass

    await page.wait_for_timeout(3500)

    # ========================================================
    # CHECK RESULT
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

    # Kalau masih ada tulisan kode konfirmasi,
    # anggap belum berhasil dan tetap pertahankan session.

    if await confirmation_page_detected(page):
        session.state = "WAITING_OTP"

        return (
            "Kode sudah dimasukkan ke 6 kotak, "
            "tetapi halaman masih berada di tahap konfirmasi."
        )

    session.state = "DONE"

    return (
        "Kode konfirmasi berhasil dimasukkan "
        "dan proses XL dilanjutkan."
    )


# ============================================================
# RESEND CODE
# ============================================================

async def resend_code(session):
    page = session.page

    try:
        locator = page.get_by_text(
            "Kirim Ulang Kode",
            exact=False
        )

        for i in range(await locator.count()):
            item = locator.nth(i)

            if await item.is_visible():
                await item.click()

                await page.wait_for_timeout(1500)

                session.state = "WAITING_OTP"

                return (
                    "Kode konfirmasi baru sudah diminta. "
                    "Silakan cek email."
                )

    except Exception:
        pass

    raise RuntimeError(
        "Tombol Kirim Ulang Kode tidak ditemukan."
    )
