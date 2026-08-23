async def submit_otp(session, code):
    code = re.sub(r"[^A-Za-z0-9]", "", code).upper()

    if not re.fullmatch(r"[A-Z0-9]{6}", code):
        raise ValueError("Kode konfirmasi harus 6 karakter huruf/angka.")

    page = session.page

    inputs = page.locator("input:visible")

    count = await inputs.count()

    if count < 6:
        raise RuntimeError(
            f"Hanya menemukan {count} kotak kode konfirmasi."
        )

    # Isi satu karakter per kotak
    for i, char in enumerate(code):
        box = inputs.nth(i)

        await box.click()
        await box.fill(char)

        await page.wait_for_timeout(100)

    await page.wait_for_timeout(500)

    # Biasanya OTP model seperti ini auto-submit.
    # Kalau ada tombol konfirmasi, klik.
    buttons = [
        page.get_by_role(
            "button",
            name=re.compile(
                r"konfirmasi|verifikasi|lanjut",
                re.I
            )
        ),
        page.locator("button[type='submit']")
    ]

    for loc in buttons:
        try:
            for i in range(await loc.count()):
                btn = loc.nth(i)

                if (
                    await btn.is_visible()
                    and await btn.is_enabled()
                ):
                    await btn.click()
                    break
        except Exception:
            pass

    await page.wait_for_timeout(3000)

    text = (
        await page.locator("body").inner_text()
    ).lower()

    if any(x in text for x in [
        "kode salah",
        "kode tidak valid",
        "invalid code",
        "expired",
        "kedaluwarsa"
    ]):
        session.state = "WAITING_OTP"
        return "Kode ditolak. Coba kode yang benar."

    session.state = "DONE"

    return "Kode konfirmasi berhasil dimasukkan."
