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

        for obj, method in (
            (self.context, "close"),
            (self.browser, "close"),
            (self.pw, "stop"),
        ):

            try:
                await getattr(
                    obj,
                    method
                )()

            except Exception:
                pass


async def visible_inputs(page):

    loc = page.locator(
        "input:visible"
    )

    return [
        loc.nth(i)
        for i in range(
            await loc.count()
        )
    ]


async def click_lanjut(page):

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

    for loc in candidates:

        try:

            for i in range(
                await loc.count()
            ):

                button = loc.nth(i)

                if not await button.is_visible():
                    continue

                await button.scroll_into_view_if_needed()

                for _ in range(20):

                    if await button.is_enabled():

                        await button.click()

                        return True

                    await page.wait_for_timeout(
                        200
                    )

        except Exception:
            pass

    return False


async def confirmation_stage(page):

    try:

        body = (
            await page
            .locator("body")
            .inner_text()
        ).lower()

        markers = [

            "kode konfirmasi",

            "kode verifikasi",

            "verification code",

            "kirim ulang kode",
        ]

        if any(
            marker in body
            for marker in markers
        ):
            return True

    except Exception:
        pass

    inputs = await visible_inputs(
        page
    )

    return len(inputs) >= 4


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
        pw,
        browser,
        context,
        page
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

        # XL form:
        # input 0 = Nama Lengkap
        # input 1 = Email
        # input 2 = Nomor WhatsApp

        inputs = await visible_inputs(
            page
        )

        if len(inputs) < 3:

            raise RuntimeError(
                f"FORM_FAIL hanya menemukan "
                f"{len(inputs)} input."
            )

        await inputs[0].fill(
            full_name
        )

        await page.wait_for_timeout(
            200
        )

        await inputs[1].fill(
            email
        )

        await page.wait_for_timeout(
            200
        )

        await inputs[2].fill(
            whatsapp
        )

        # trigger validation

        try:

            await inputs[2].press(
                "Tab"
            )

        except Exception:
            pass

        await page.wait_for_timeout(
            700
        )

        if not await click_lanjut(
            page
        ):

            raise RuntimeError(
                "BUTTON_FAIL tombol Lanjut "
                "tidak dapat diklik."
            )

        deadline = (
            asyncio
            .get_running_loop()
            .time()
            + 40
        )

        while (
            asyncio
            .get_running_loop()
            .time()
            < deadline
        ):

            if await confirmation_stage(
                page
            ):

                session.state = (
                    "WAITING_OTP"
                )

                return session

            await asyncio.sleep(
                0.8
            )

        raise RuntimeError(
            "CODE_STAGE_FAIL halaman "
            "kode konfirmasi tidak terdeteksi."
        )

    except Exception:

        await session.close()

        raise


async def submit_otp(
    session,
    code
):

    code = re.sub(
        r"[^A-Za-z0-9]",
        "",
        code
    ).upper()

    if not re.fullmatch(
        r"[A-Z0-9]{4,8}",
        code
    ):

        raise ValueError(
            "Kode harus 4–8 "
            "karakter huruf/angka."
        )

    page = session.page

    #
    # First try single confirmation-code input
    #

    single_input = None

    selectors = [

        "input[autocomplete='one-time-code']",

        "input[name*='otp' i]",

        "input[id*='otp' i]",

        "input[name*='code' i]",

        "input[id*='code' i]",
    ]

    for selector in selectors:

        try:

            loc = page.locator(
                selector
            )

            if (
                await loc.count()
                and
                await loc.first.is_visible()
            ):

                single_input = (
                    loc.first
                )

                break

        except Exception:
            pass

    if single_input:

        await single_input.fill(
            code
        )

    else:

        #
        # Multiple character boxes
        #

        inputs = await visible_inputs(
            page
        )

        if len(inputs) < len(code):

            raise RuntimeError(
                f"CODE_INPUT_FAIL "
                f"hanya menemukan "
                f"{len(inputs)} input."
            )

        for box, character in zip(
            inputs[:len(code)],
            code
        ):

            await box.fill(
                character
            )

    await page.wait_for_timeout(
        700
    )

    #
    # Confirmation button
    #

    candidates = [

        page.get_by_role(
            "button",
            name=re.compile(
                r"verifikasi|konfirmasi|lanjut",
                re.I
            )
        ),

        page.locator(
            "button[type='submit']"
        ),
    ]

    clicked = False

    for loc in candidates:

        try:

            for i in range(
                await loc.count()
            ):

                button = loc.nth(i)

                if (
                    await button.is_visible()
                    and
                    await button.is_enabled()
                ):

                    await button.click()

                    clicked = True

                    break

            if clicked:
                break

        except Exception:
            pass

    #
    # Some confirmation pages auto-submit
    #

    await page.wait_for_timeout(
        3500
    )

    try:

        body = (
            await page
            .locator("body")
            .inner_text()
        ).lower()

    except Exception:

        body = ""

    errors = [

        "kode salah",

        "kode tidak valid",

        "invalid code",

        "expired",

        "kedaluwarsa",
    ]

    if any(
        error in body
        for error in errors
    ):

        session.state = (
            "WAITING_OTP"
        )

        return (
            "Kode ditolak. "
            "Masukkan kode yang benar."
        )

    session.state = "DONE"

    return (
        "Kode konfirmasi sudah dimasukkan "
        "dan proses klaim dilanjutkan."
    )
