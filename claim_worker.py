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
    for pattern in keyword_patterns:
        try:
            loc = page.get_by_label(re.compile(pattern, re.I))
            for i in range(await loc.count()):
                el = loc.nth(i)
                if await el.is_visible() and await el.is_enabled():
                    return el
        except Exception:
            pass

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
            "kode verifikasi",
            "verification code",
            "kirim ulang kode",
        )):
            return True
    except Exception:
        pass

    inputs = await _visible_inputs(page)
    return len(inputs) >= 4

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

        name_input = await _find_input(page, ["nama lengkap", "nama"])
        email_input = await _find_input(page, ["email"])
        wa_input = await _find_input(page, ["nomor whatsapp", "whatsapp", "nomor"])

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
        await email_input.fill(email)
        await wa_input.fill(whatsapp)

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

        raise RuntimeError("OTP_STAGE_FAIL halaman kode konfirmasi tidak terdeteksi")

    except Exception:
        await s.close()
        raise

async def submit_otp(session: ClaimSession, code: str) -> str:
    code = re.sub(r"[^A-Za-z0-9]", "", code).upper()

    if not re.fullmatch(r"[A-Z0-9]{6}", code):
        raise ValueError("Kode konfirmasi harus tepat 6 karakter huruf/angka.")

    page = session.page

    # XL confirmation UI: six separate visible boxes, one character per box.
    inputs = page.locator("input:visible")
    count = await inputs.count()

    if count < 6:
        raise RuntimeError(
            f"CODE_INPUT_FAIL hanya menemukan {count} kotak kode konfirmasi."
        )

    # Fill exactly one character into each of the first six visible boxes.
    for i, char in enumerate(code):
        box = inputs.nth(i)
        await box.click()
        await box.fill(char)
        await page.wait_for_timeout(100)

    await page.wait_for_timeout(700)

    # The page may auto-submit. If a confirmation button exists, click it.
    clicked = False
    for loc in (
        page.get_by_role(
            "button",
            name=re.compile(r"verifikasi|konfirmasi|lanjut", re.I),
        ),
        page.locator("button[type='submit']"),
    ):
        try:
            for i in range(await loc.count()):
                btn = loc.nth(i)
                if await btn.is_visible() and await btn.is_enabled():
                    await btn.click()
                    clicked = True
                    break
            if clicked:
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
        "kode tidak valid",
        "invalid code",
        "expired",
        "kedaluwarsa",
    )):
        session.state = "WAITING_OTP"
        return "Kode ditolak. Masukkan kode yang benar."

    session.state = "DONE"
    return "Kode konfirmasi berhasil dimasukkan."
