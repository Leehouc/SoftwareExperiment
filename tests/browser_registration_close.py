"""真实 HTTP 与内存数据库验证关闭阶段、刷新、过期页面权限及结算。"""

import os
from datetime import date, timedelta
from unittest.mock import patch

from tests.test_session_security import SessionSecurityTests, admin


def main():
    from playwright.sync_api import sync_playwright, expect

    fixture = SessionSecurityTests()
    fixture.setUp()
    try:
        today = date.today()
        fixture.db.execute("UPDATE registration_periods SET registration_status = 'OPEN', registration_start_at = ?, "
                           "registration_end_at = ?, add_drop_start_at = ?, add_drop_end_at = ?",
                           tuple((today + timedelta(days=offset)).isoformat() for offset in (-20, -5, -3, 0)))
        fixture.db.commit()
        fixture.catalog.update(departments=[], faculty=[])
        for offering in fixture.catalog["courseOfferings"][:4]:
            offering["enrolledCount"] = 2
        mock = patch.object(admin, "_fetch_catalog", lambda: fixture.catalog)
        mock.start()
        fixture.addCleanup(mock.stop)
        with sync_playwright() as playwright:
            executable = os.environ.get("CHROME_EXECUTABLE")
            browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("dialog", lambda dialog: dialog.accept())
                page.goto(fixture.urls["admin"])
                page.locator('#login-form input[name="username"]').fill("registrar")
                page.locator('#login-form input[name="password"]').fill(fixture.password)
                page.locator('#login-form button[type="submit"]').click()
                expect(page.locator("#app-shell")).to_be_visible()
                page.locator('[data-view="registration"]').click()
                close = page.locator('[data-action="close-registration"]')
                refresh = page.locator('[data-action="refresh-registration"]')
                expect(close).to_be_disabled()
                expect(page.locator("#page-content")).to_contain_text("尚未全部结束")
                expect(page.locator("#page-content")).to_contain_text((today + timedelta(days=1)).isoformat())
                assert fixture.db.execute("SELECT COUNT(*) FROM billing_transactions").fetchone()[0] == 0

                fixture.db.execute("UPDATE registration_periods SET add_drop_end_at = ?",
                                   ((today - timedelta(days=1)).isoformat(),))
                fixture.db.commit()
                refresh.click()
                expect(close).to_be_enabled()
                # 页面已拿到可关闭权限后，延长窗口；写接口必须重新读取并拒绝。
                fixture.db.execute("UPDATE registration_periods SET add_drop_end_at = ?",
                                   ((today + timedelta(days=1)).isoformat(),))
                fixture.db.commit()
                before = list(fixture.db.iterdump())
                close.click()
                expect(page.locator("#toast-region")).to_contain_text("尚未全部结束")
                expect(close).to_be_disabled()
                assert list(fixture.db.iterdump()) == before

                fixture.db.execute("UPDATE registration_periods SET add_drop_end_at = ?",
                                   ((today - timedelta(days=1)).isoformat(),))
                fixture.db.commit()
                refresh.click()
                expect(close).to_be_enabled()
                close.click()
                expect(page.locator("#page-content")).to_contain_text("不能重复结算")
                expect(close).to_have_count(0)
                assert fixture.db.execute("SELECT registration_status FROM registration_periods").fetchone()[0] == "CLOSED"
                assert fixture.db.execute("SELECT COUNT(*), SUM(amount_cents) FROM billing_transactions").fetchone()[:] == (1, 180000)
                assert not errors, errors
                print("Browser close regression passed: inclusive deadline, first allowed date, refresh, stale permission rejected atomically, final billing, no page errors")
            finally:
                browser.close()
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    main()
