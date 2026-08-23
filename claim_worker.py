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
# CLAIM SESSION
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
# GET VISIBLE INPUTS
# ============================================================

async def get_visible_inputs(page):

    locator = page.locator(
        "input:visible"
    )

    count = await locator.count()

    return [
        locator.nth(i)
        for i in range(count)
    ]


# ============================================================
# CLICK LANJUT
# ============================================================

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

    for locator in candidates:

        try:

            count = await locator.count()

            for i in range(count):

                button = locator.nth(i)

                if not await button.is_visible():
                    continue

                await button.scroll_into_view_if_needed()

                # Tunggu sampai tombol aktif
                for _ in range(30):

                    if await button.is_enabled():

                        await button.click()

                        return True

                    await page.wait_for_timeout(
                        200
                    )

        except Exception:
            continue

    return False


# ============================================================
# DETECT CONFIRMATION PAGE
# ============================================================

async def confirmation_page_detected(page):

    try:

        body = (
            await page
            .locator("body")
            .inner_text()
        ).lower()

        markers = [
            "kode konfirmasi",
            "kirim ulang kode",
            "kode verifikasi",
            "verification code",
        ]

        if any(
            marker in body
            for marker in markers
        ):
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
        # GET FORM INPUTS
        #
        # Screenshot XL:
        #
        # input 0 = Nama Lengkap
        # input 1 = Email
        # input 2 = Nomor WhatsApp
        # ====================================================

        inputs = await get_visible_inputs(
            page
        )

        if len(inputs) < 3:

            raise RuntimeError(
                "FORM_FAIL: "
                f"hanya menemukan {len(inputs)} "
                "input yang terlihat."
            )


        name_input = inputs[0]
        email_input = inputs[1]
        whatsapp_input = inputs[2]


        # ====================================================
        # NAME
        # ====================================================

        await name_input.click()

        await name_input.fill(
            full_name
        )

        await page.wait_for_timeout(
            300
        )


        # ====================================================
        # EMAIL
        # ====================================================

        await email_input.click()

        await email_input.fill(
            email
        )

        await page.wait_for_timeout(
            300
        )


        # ====================================================
        # WHATSAPP
        # ====================================================

        await whatsapp_input.click()

        await whatsapp_input.fill(
            whatsapp
        )

        await page.wait_for_timeout(
            300
        )


        # Trigger blur / React validation
        try:

            await whatsapp_input.press(
                "Tab"
            )

        except Exception:
            pass


        await page.wait_for_timeout(
            1000
        )


        # ====================================================
        # CLICK LANJUT
        # ====================================================

        lanjut_clicked = await click_lanjut(
            page
        )

        if not lanjut_clicked:

            raise RuntimeError(
                "BUTTON_FAIL: "
                "tombol Lanjut tidak ditemukan "
                "atau belum aktif."
            )


        # ====================================================
        # WAIT FOR CONFIRMATION PAGE
        # ====================================================

        deadline = (
            asyncio
            .get_running_loop()
            .time()
            + 45
        )


        while (
            asyncio
            .get_running_loop()
            .time()
            < deadline
        ):

            if await confirmation_page_detected(
                page
            ):

                session.state = (
                    "WAITING_OTP"
                )

                return session


            await asyncio.sleep(
                0.5
            )


        raise RuntimeError(
            "CODE_STAGE_FAIL: "
            "halaman Kode Konfirmasi "
            "tidak terdeteksi."
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

    # ========================================================
    # NORMALIZE CODE
    #
    # XL may send:
    #
    # CKBILA
    # YKZVAT
    # QQAYRV
    #
    # Letters MUST NOT be removed.
    # ========================================================

    code = re.sub(
        r"[^A-Za-z0-9]",
        "",
        code
    ).upper()


    # Exactly 6 alphanumeric characters

    if not re.fullmatch(
        r"[A-Z0-9]{6}",
        code
    ):

        raise ValueError(
            "Kode konfirmasi harus tepat "
            "6 karakter huruf/angka."
        )


    page = session.page


    # ========================================================
    # WAIT UNTIL CONFIRMATION PAGE READY
    # ========================================================

    await page.wait_for_timeout(
        1000
    )


    # ========================================================
    # GET THE SIX CONFIRMATION BOXES
    # ========================================================

    inputs = page.locator(
        "input:visible"
    )

    count = await inputs.count()


    if count < 6:

        raise RuntimeError(
            "CODE_INPUT_FAIL: "
            f"seharusnya ada 6 kotak, "
            f"tetapi hanya menemukan {count}."
        )


    # ========================================================
    # IMPORTANT
    #
    # Example:
    #
    # CKBILA
    #
    # BOX 1 -> C
    # BOX 2 -> K
    # BOX 3 -> B
    # BOX 4 -> I
    # BOX 5 -> L
    # BOX 6 -> A
    #
    # Every box is clicked explicitly.
    # We do NOT paste the whole code.
    # ========================================================

    for index in range(6):

        character = code[index]

        box = inputs.nth(
            index
        )


        # Make sure box is visible

        await box.scroll_into_view_if_needed()


        # ----------------------------------------------------
        # CLICK THIS SPECIFIC BOX
        # ----------------------------------------------------

        await box.click()


        await page.wait_for_timeout(
            150
        )


        # ----------------------------------------------------
        # CLEAR THIS SPECIFIC BOX
        # ----------------------------------------------------

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
            100
        )


        # ----------------------------------------------------
        # TYPE ONLY ONE CHARACTER
        # ----------------------------------------------------

        await box.type(
            character,
            delay=120
        )


        # ----------------------------------------------------
        # WAIT BEFORE CLICKING NEXT BOX
        # ----------------------------------------------------

        await page.wait_for_timeout(
            350
        )


    # ========================================================
    # GIVE FRONTEND TIME TO PROCESS CODE
    # ========================================================

    await page.wait_for_timeout(
        1000
    )


    # ========================================================
    # OPTIONAL CONFIRMATION BUTTON
    #
    # Some OTP interfaces submit automatically.
    # If XL provides a button, click it.
    # ========================================================

    button_clicked = False


    button_regex = re.compile(
        r"konfirmasi|verifikasi|lanjut",
        re.I
    )


    try:

        buttons = page.get_by_role(
            "button",
            name=button_regex
        )


        button_count = await buttons.count()


        for i in range(
            button_count
        ):

            button = buttons.nth(
                i
            )


            if not await button.is_visible():

                continue


            if not await button.is_enabled():

                continue


            await button.scroll_into_view_if_needed()

            await button.click()

            button_clicked = True

            break


    except Exception:

        pass


    # ========================================================
    # FALLBACK SUBMIT BUTTON
    # ========================================================

    if not button_clicked:

        try:

            submit_buttons = page.locator(
                "button[type='submit']:visible"
            )


            submit_count = (
                await submit_buttons.count()
            )


            for i in range(
                submit_count
            ):

                button = submit_buttons.nth(
                    i
                )


                if (
                    await button.is_visible()
                    and
                    await button.is_enabled()
                ):

                    await button.click()

                    button_clicked = True

                    break


        except Exception:

            pass


    # ========================================================
    # WAIT FOR RESULT
    # ========================================================

    await page.wait_for_timeout(
        3500
    )


    # ========================================================
    # READ PAGE RESULT
    # ========================================================

    try:

        body_text = (
            await page
            .locator("body")
            .inner_text()
        ).lower()

    except Exception:

        body_text = ""


    # ========================================================
    # INVALID / EXPIRED CODE
    # ========================================================

    error_markers = [
        "kode salah",
        "kode tidak valid",
        "invalid code",
        "kode kadaluarsa",
        "kode kedaluwarsa",
        "kode telah kedaluwarsa",
        "expired",
    ]


    if any(
        marker in body_text
        for marker in error_markers
    ):

        session.state = (
            "WAITING_OTP"
        )

        return (
            "Kode ditolak oleh XL. "
            "Silakan masukkan kode terbaru."
        )


    # ========================================================
    # CHECK WHETHER WE ARE STILL ON CODE PAGE
    # ========================================================

    try:

        still_confirmation = (
            await confirmation_page_detected(
                page
            )
        )

    except Exception:

        still_confirmation = False


    if still_confirmation:

        session.state = (
            "WAITING_OTP"
        )

        return (
            "Kode sudah diisi ke 6 kotak, "
            "tetapi halaman masih berada "
            "di tahap konfirmasi."
        )


    # ========================================================
    # SUCCESS
    # ========================================================

    session.state = "DONE"


    return (
        "Kode konfirmasi berhasil dimasukkan "
        "dan halaman XL telah melanjutkan proses."
    )
