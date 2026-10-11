"""真实 HTTP 与内存数据库的教务员建教师、资格维护、教师选授流程。"""

import os
from unittest.mock import patch

from tests.test_session_security import SessionSecurityTests, admin, teacher, student


def main():
    from playwright.sync_api import sync_playwright, expect

    fixture = SessionSecurityTests()
    fixture.setUp()
    try:
        fixture.catalog.update(departments=[{"departmentId": "CS", "name": "Computer Science"}], faculty=[])
        fixture.catalog["courseOfferings"].append({"offeringId": "FREE", "semesterId": "TERM", "courseId": "C1",
                                                  "status": "OPEN", "capacity": 10, "days": []})
        fixture.db.execute("UPDATE registration_periods SET registration_status = 'OPEN'")
        fixture.db.commit()
        for module, name, value in ((admin, "_fetch_catalog", lambda: fixture.catalog),
                                    (teacher, "_fetch_catalog", lambda: fixture.catalog),
                                    (teacher, "transaction", student.transaction)):
            mock = patch.object(module, name, value)
            mock.start()
            fixture.addCleanup(mock.stop)
        with sync_playwright() as playwright:
            executable = os.environ.get("CHROME_EXECUTABLE")
            browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
            try:
                context = browser.new_context()
                admin_page = context.new_page()
                errors = []
                admin_page.on("pageerror", lambda error: errors.append(str(error)))
                admin_page.goto(fixture.urls["admin"])
                admin_page.locator('#login-form input[name="username"]').fill("registrar")
                admin_page.locator('#login-form input[name="password"]').fill(fixture.password)
                admin_page.locator('#login-form button[type="submit"]').click()
                expect(admin_page.locator("#app-shell")).to_be_visible()
                admin_page.locator('[data-view="professors"]').click()
                admin_page.locator('[data-action="add"]').click()
                form = admin_page.locator("#record-form")
                form.locator('[name="fullName"]').fill("New Teacher")
                form.locator('[name="dateOfBirth"]').fill("1980-01-01")
                form.locator('[name="departmentId"]').select_option("CS")
                form.locator('[name="username"]').fill("new.teacher")
                form.locator('[name="password"]').fill("TeacherPassword1!")
                form.locator('[name="eligibleCourseIds"][value="C1"]').check()
                form.locator('[name="eligibleCourseIds"][value="C2"]').check()
                form.locator('button[type="submit"]').click()
                expect(admin_page.locator("#record-dialog")).not_to_be_visible()
                row = admin_page.locator("tbody tr").filter(has_text="New Teacher")
                expect(row).to_contain_text("授课资格（2）")
                professor_id = fixture.db.execute("SELECT professor_id FROM professors p JOIN app_users u "
                                                  "ON u.user_id=p.user_id WHERE username='new.teacher'").fetchone()[0]
                assert fixture.db.execute("SELECT COUNT(*) FROM professor_course_eligibility WHERE professor_id=?",
                                          (professor_id,)).fetchone()[0] == 2
                row.locator('[data-action="eligibility"]').click()
                expect(form.locator('[value="C1"][name="eligibleCourseIds"]')).to_be_checked()
                expect(form.locator('[value="C2"][name="eligibleCourseIds"]')).to_be_checked()
                admin_page.locator('.close-dialog').first.click()

                teacher_page = context.new_page()
                teacher_page.on("pageerror", lambda error: errors.append(str(error)))
                teacher_page.goto(fixture.urls["teacher"])
                teacher_page.locator("#username").fill("new.teacher")
                teacher_page.locator("#password").fill("TeacherPassword1!")
                teacher_page.locator("#login-button").click()
                expect(teacher_page.locator("#portal")).to_be_visible()
                teacher_page.locator('[data-page="teaching"]').click()
                teacher_page.locator('[data-offering-id="FREE"]').click()
                teacher_page.locator("#save-teaching").click()
                expect(teacher_page.locator("#page-alert")).to_contain_text("已保存")

                row.locator('[data-action="eligibility"]').click()
                form.locator('[name="eligibleCourseIds"][value="C1"]').uncheck()
                form.locator('button[type="submit"]').click()
                expect(admin_page.locator("#record-dialog")).not_to_be_visible()
                expect(row).to_contain_text("授课资格（1）")
                teacher_page.reload()
                expect(teacher_page.locator("#portal")).to_be_visible()
                teacher_page.locator('[data-page="teaching"]').click()
                retained = teacher_page.locator(".course-card").filter(has=teacher_page.locator('[data-offering-id="FREE"]'))
                expect(retained).to_contain_text("资格已撤销，仅保留已有授课")
                expect(retained.locator("button")).to_contain_text("取消选择")
                teacher_page.locator("#save-teaching").click()
                expect(teacher_page.locator("#page-alert")).to_contain_text("已保存")
                retained.locator("button").click()
                teacher_page.locator("#save-teaching").click()
                expect(teacher_page.locator('[data-offering-id="FREE"]')).to_have_count(0)

                row.locator('[data-action="eligibility"]').click()
                form.locator('[name="eligibleCourseIds"][value="C2"]').uncheck()
                form.locator('button[type="submit"]').click()
                expect(row).to_contain_text("授课资格（0）")
                expect(admin_page.locator("#toast-region")).to_contain_text("尚未配置授课资格")
                admin_page.set_viewport_size({"width": 390, "height": 844})
                row.locator('[data-action="eligibility"]').click()
                expect(admin_page.locator("#eligibility-fields")).to_be_visible()
                assert admin_page.locator("#record-dialog").evaluate("element => element.scrollWidth <= element.clientWidth")
                assert not errors, errors
                print("Browser eligibility regression passed: teacher creation with two qualifications, login and teaching, existing qualification edit, retained teaching, withdrawal, empty warning, mobile, no page errors")
            finally:
                browser.close()
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    main()
