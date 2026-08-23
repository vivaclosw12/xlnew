import asyncio
import os
import re
from dataclasses import dataclass
from playwright.async_api import async_playwright

CLAIM_URL = os.getenv("CLAIM_URL", "https://www.xl.co.id/esim-trial/claim")
HEADLESS = os.getenv("HEADLESS", "true").lower() not in {"false", "0", "no"}
TIMEOUT = int(os.getenv("BROWSER_TIMEOUT_MS", "45000"))

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
                await getattr(obj, method)()
            except Exception:
                pass


async def _visible_inputs(page):
    loc = page.locator("input:visible")
    return [loc.nth(i) for i in range(await loc.count())]


async def _find_input(page, keyword_patterns):
    # First: labels
    for pattern in keyword_patterns:
        try:
            loc = page.get_by_label(re.compile(pattern, re.I))
            for i in range(await loc.count()):
                el = loc.nth(i)
                if await el.is_visible() and await el.is_enabled():
                    return el
        except Exception:
            pass

    # Second: placeholders / names / ids
    for pattern in keyword_patterns:
        for attr in ("placeholder", "name", "id"):
            try:
                loc = page.locator(f"input[{attr}*='{pattern}' i]")
                for i in range(await loc.count()):
                    el = loc.nth(i)
                    if await el.is_visible() and await el.is_enabled():
                        return el
            except Exception:
                pass
    return None


async def _click_lanjut(page):
    candidates = [
        page.get_by_role("button", name=re.compile(r"^\s*lanjut\s*$", re.I)),
        page.locator("button").filter(has_text=re.compile(r"^\s*lanjut\s*$", re.I)),
        page.locator("button[type='submit']"),
    ]
    for loc in candidates:
        try:
            for i in range(await loc.count()):
                el = loc.nth(i)
                if await el.is_visible():
                    await el.scroll_into_view_if_needed()
                    for _ in range(20):
                        if await el.is_enabled():
                            await el.click()
                            return True
                        await page.wait_for_timeout(200)
        except Exception:
            pass
    return False


async def _otp_stage(page):
    try:
        body = (await page.locator("body").inner_text()).lower()
        if any(s in body for s in (
            "kode konfirmasi",
            "masukkan 6 digit",
            "kirim ulang kode",
            "kode verifikasi",
            "verification code",
        )):
            return True
    except Exception:
        pass

    inputs = await _visible_inputs(page)
    if len(inputs) >= 6:
        return True

    return False


async def start_claim(full_name: str, email: str, whatsapp: str) -> ClaimSession:
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=HEADLESS)
    context = await browser.new_context(
        locale="id-ID",
        viewport={"width": 1365, "height": 960},
    )
    page = await context.new_page()
    page.set_default_timeout(TIMEOUT)

    s = ClaimSession(pw, browser, context, page)

    try:
        await page.goto(CLAIM_URL, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(1500)

        # Try semantic detection first.
        name_input = await _find_input(page, ["nama lengkap", "nama"])
        email_input = await _find_input(page, ["email"])
        wa_input = await _find_input(page, ["nomor whatsapp", "whatsapp", "nomor"])

        # Hard fallback to the exact page structure shown by the user:
        # 1st visible input = Nama Lengkap
        # 2nd visible input = Email
        # 3rd visible input = Nomor WhatsApp
        inputs = await _visible_inputs(page)

        if name_input is None and len(inputs) >= 1:
            name_input = inputs[0]
        if email_input is None and len(inputs) >= 2:
            email_input = inputs[1]
        if wa_input is None and len(inputs) >= 3:
            wa_input = inputs[2]

        if not all([name_input, email_input, wa_input]):
            raise RuntimeError(
                f"FORM_FAIL visible_inputs={len(inputs)} url={page.url}"
            )

        await name_input.fill(full_name)
        await page.wait_for_timeout(200)
        await email_input.fill(email)
        await page.wait_for_timeout(200)
        await wa_input.fill(whatsapp)
        await page.wait_for_timeout(500)

        # Commit React/onBlur validation.
        try:
            await wa_input.press("Tab")
        except Exception:
            pass

        await page.wait_for_timeout(700)

        if not await _click_lanjut(page):
            raise RuntimeError("BUTTON_FAIL tombol Lanjut tidak dapat diklik")

        deadline = asyncio.get_running_loop().time() + 40
        while asyncio.get_running_loop().time() < deadline:
            if await _otp_stage(page):
                s.state = "WAITING_OTP"
                return s
            await asyncio.sleep(0.8)

        try:
            body = " ".join((await page.locator("body").inner_text()).split())[:300]
        except Exception:
            body = ""

        raise RuntimeError(f"OTP_STAGE_FAIL page={body}")

    except Exception:
        await s.close()
        raise


async def submit_otp(session: ClaimSession, otp: str) -> str:
    otp = re.sub(r"\D", "", otp)

    if not re.fullmatch(r"\d{6}", otp):
        raise ValueError("OTP harus tepat 6 digit.")

    page = session.page

    # Single OTP field if site uses one.
    for selector in (
        "input[autocomplete='one-time-code']",
        "input[name*='otp' i]",
        "input[id*='otp' i]",
    ):
        try:
            loc = page.locator(selector)
            if await loc.count() and await loc.first.is_visible():
                await loc.first.fill(otp)
                break
        except Exception:
            pass
    else:
        # Screenshot shows six OTP boxes.
        inputs = await _visible_inputs(page)
        if len(inputs) < 6:
            raise RuntimeError(f"OTP_INPUT_FAIL hanya menemukan {len(inputs)} input")

        boxes = inputs[:6]
        for box, digit in zip(boxes, otp):
            await box.fill(digit)

    await page.wait_for_timeout(800)

    # Some pages auto-submit. Otherwise click any confirmation action.
    for loc in (
        page.get_by_role("button", name=re.compile(r"verifikasi|konfirmasi|lanjut", re.I)),
        page.locator("button[type='submit']"),
    ):
        try:
            for i in range(await loc.count()):
                el = loc.nth(i)
                if await el.is_visible() and await el.is_enabled():
                    await el.click()
                    raise StopAsyncIteration
        except StopAsyncIteration:
            break
        except Exception:
            pass

    await page.wait_for_timeout(3500)

    try:
        text = (await page.locator("body").inner_text()).lower()
    except Exception:
        text = ""

    if any(x in text for x in (
        "kode salah",
        "otp salah",
        "kode tidak valid",
        "invalid code",
        "expired",
        "kedaluwarsa",
    )):
        session.state = "WAITING_OTP"
        return "OTP ditolak. Masukkan kode yang benar."

    session.state = "DONE"
    return "OTP sudah dimasukkan dan proses klaim dilanjutkan."
