"""Playwright + 真实门户 HTTP 服务、内存数据库验证登录与撤销后的页面行为。"""

import os
from datetime import date, timedelta
from unittest.mock import patch

from tests.test_session_security import SessionSecurityTests, admin, encoded_password
from teacher import server as teacher


def main():
    from playwright.sync_api import sync_playwright, expect

    fixture = SessionSecurityTests()
    fixture.setUp()
    try:
        fixture.db.execute("UPDATE enrollments SET enrollment_status = 'COMPLETED' WHERE offering_id = 'O1'")
        fixture.db.execute("INSERT INTO course_grades (enrollment_id, professor_id, letter_grade) "
                           "SELECT enrollment_id, 'P', 'A' FROM enrollments WHERE offering_id = 'O1'")
        fixture.db.commit()
        fixture.catalog["semesters"][0].update(name="上一学期", endDate=(date.today() - timedelta(days=1)).isoformat())
        fixture.catalog.update(departments=[], faculty=[])
        for module in (teacher, admin):
            mock = patch.object(module, "_fetch_catalog", lambda: fixture.catalog)
            mock.start()
            fixture.addCleanup(mock.stop)
        fixture.status_mock.return_value = {
            "ok": True, "database_name": "test_database", "server_version": "test", "mysql_account": "test_account",
            "tables": [{"name": "course_grades", "label": "课程成绩", "count": 1}],
        }
        with sync_playwright() as playwright:
            executable = os.environ.get("CHROME_EXECUTABLE")
            browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(fixture.urls["student"])
                page.locator("#username").fill("student")
                page.locator("#password").fill(fixture.password)
                page.locator("#login-button").click()
                expect(page.locator("#portal")).to_be_visible()
                page.locator('.nav-item[data-page="transcript"]').click()
                expect(page.locator("#transcript-rows")).to_contain_text("Course 1")
                admin._deactivate_profile("student", "S", {"user": {"userId": "R"}})
                page.evaluate("refreshAvailability()")
                expect(page.locator("#login-screen")).to_be_visible()
                expect(page.locator("#portal")).to_be_hidden()
                expect(page.locator("#transcript-rows")).to_be_empty()
                expect(page.locator("#login-message")).to_contain_text("失效")

                page.goto(fixture.urls["teacher"])
                page.locator("#username").fill("professor")
                page.locator("#password").fill(fixture.password)
                page.locator("#login-button").click()
                expect(page.locator("#portal")).to_be_visible()
                page.locator('.nav-item[data-page="grades"]').click()
                expect(page.locator("#roster-rows")).to_contain_text("Student")
                fixture.db.execute("UPDATE professors SET status = 'INACTIVE'")
                fixture.db.commit()
                page.evaluate("loadRoster('O1')")
                expect(page.locator("#login-screen")).to_be_visible()
                expect(page.locator("#roster-rows")).to_be_empty()

                page.goto(fixture.urls["checker"])
                expect(page.locator("#login-panel")).to_be_visible()
                expect(page.locator("#inspection-panel")).to_be_hidden()
                fixture.status_mock.assert_not_called()
                page.locator("#username").fill("professor")
                page.locator("#password").fill(fixture.password)
                page.locator("#login-button").click()
                expect(page.locator("#login-message")).to_contain_text("没有教务员权限")
                fixture.status_mock.assert_not_called()
                page.locator("#username").fill("registrar")
                page.locator("#password").fill(fixture.password)
                page.locator("#login-button").click()
                expect(page.locator("#inspection-panel")).to_be_visible()
                expect(page.locator("#preview-head")).to_contain_text("letter_grade")
                expect(page.locator("#preview-body")).to_contain_text("A")

                new_password = "ResetPassword2!"
                fixture.db.execute("UPDATE app_users SET password_hash = ? WHERE user_id = 'R'",
                                   (encoded_password(new_password),))
                fixture.db.commit()
                page.locator("#refresh-button").click()
                expect(page.locator("#login-panel")).to_be_visible()
                expect(page.locator("#preview-body")).to_be_empty()
                expect(page.locator("#detail-user")).to_have_text("—")
                page.locator("#password").fill(new_password)
                page.locator("#login-button").click()
                expect(page.locator("#inspection-panel")).to_be_visible()
                expect(page.locator("#preview-body")).to_contain_text("A")
                page.locator("#logout-button").click()
                expect(page.locator("#login-panel")).to_be_visible()
                expect(page.locator("#preview-body")).to_be_empty()
                page.reload()
                expect(page.locator("#inspection-panel")).to_be_hidden()

                page.goto(fixture.urls["admin"])
                page.locator('#login-form input[name="username"]').fill("registrar")
                page.locator('#login-form input[name="password"]').fill(new_password)
                page.locator('#login-form button[type="submit"]').click()
                expect(page.locator("#app-shell")).to_be_visible()
                page.locator('[data-view="students"]').click()
                page.locator('[data-action="edit"]').click()
                expect(page.locator("#record-dialog")).to_be_visible()
                page.locator('#record-form input[name="graduationDate"]').fill("2030-06-30")
                fixture.db.execute("UPDATE app_users SET is_active = 0 WHERE user_id = 'R'")
                fixture.db.commit()
                page.locator('#record-form button[type="submit"]').click()
                expect(page.locator("#login-screen")).to_be_visible()
                expect(page.locator("#record-dialog")).not_to_be_visible()
                expect(page.locator("#page-content")).to_be_empty()
                assert not errors, errors
                print("Browser security regression passed: all four services, real HTTP login and revocation, registrar-only previews, password reset, logout, no page errors")
            finally:
                browser.close()
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    main()
