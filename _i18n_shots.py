# لقطات تحقّق i18n: نفس الصفحات بالعربي (RTL) ثم الإنجليزي (LTR) بعد تبديل اللغة.
import sys
from playwright.sync_api import sync_playwright

B = "http://127.0.0.1:5079/admin/radius"
OUT = r"C:\Projects\radius-module"
PAGES = [("dashboard", "/dashboard"), ("settings_system", "/settings/system"),
         ("subscribers", "/subscribers"), ("cards", "/cards")]


def dir_of(pg):
    return pg.eval_on_selector("html", "e=>e.getAttribute('dir')")


def main() -> int:
    with sync_playwright() as p:
        br = p.chromium.launch()
        c = br.new_context(viewport={"width": 1440, "height": 1000}, locale="ar")
        pg = c.new_page()
        pg.goto(B + "/login", wait_until="networkidle")
        pg.fill('input[name=username]', "admin"); pg.fill('input[name=password]', "admin")
        pg.click('button[type=submit], input[type=submit]'); pg.wait_for_load_state("networkidle")

        # AR (default)
        for name, path in PAGES:
            try:
                pg.goto(B + path, wait_until="domcontentloaded"); pg.wait_for_timeout(500)
                pg.screenshot(path=f"{OUT}\\_i18n_ar_{name}.png")
                print(f"AR {name}: dir={dir_of(pg)}")
            except Exception as e:
                print(f"AR {name} FAIL: {e!r}")

        # switch to EN
        pg.goto(B + "/set-locale?locale=en&next=/admin/radius/dashboard", wait_until="networkidle")
        print("after switch dir:", dir_of(pg))
        for name, path in PAGES:
            try:
                pg.goto(B + path, wait_until="domcontentloaded"); pg.wait_for_timeout(500)
                pg.screenshot(path=f"{OUT}\\_i18n_en_{name}.png")
                print(f"EN {name}: dir={dir_of(pg)}")
            except Exception as e:
                print(f"EN {name} FAIL: {e!r}")

        c.close(); br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
