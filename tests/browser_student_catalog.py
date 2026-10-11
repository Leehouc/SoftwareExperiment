"""可选 Playwright 浏览器回归；使用静态学生页面及模拟 API，不连接业务数据库。

启动 student 静态 HTTP 服务后运行此脚本，可用 STUDENT_TEST_URL 指定 URL。
需要 Python playwright；CHROME_EXECUTABLE 可指定已安装的 Chromium 浏览器。
"""

import copy
import json
import os


def main():
    from playwright.sync_api import sync_playwright, expect

    initial = {
        "student": {"student_id": "S", "full_name": "Student"},
        "semesterId": "TERM", "semesters": [{"semesterId": "TERM", "name": "Current term"}],
        "period": {"registration_status": "OPEN"}, "schedule": {"status": "DRAFT"},
        "canEdit": True, "registrationOpen": True, "addDropOpen": True, "previouslySubmitted": False,
        "enrollments": [], "transcript": [], "choices": [], "offeringCounts": {},
        "teachingAssignments": {"O1": {"fullName": "Actual teacher"}},
        "catalog": {
            "courses": [{"courseId": "C1", "title": "Advanced", "departmentId": "CS", "credits": 3},
                        {"courseId": "C2", "title": "Basics", "departmentId": "CS", "credits": 3}],
            "departments": [{"departmentId": "CS", "name": "Computer Science"}],
            "faculty": [{"facultyId": "LEGACY", "name": "Wrong legacy teacher"}],
            "prerequisites": [{"courseId": "C1", "prerequisiteCourseId": "C2"}],
            "courseOfferings": [
                {"offeringId": "O1", "courseId": "C1", "semesterId": "TERM", "status": "OPEN",
                 "capacity": 2, "days": [], "facultyId": "LEGACY"},
                {"offeringId": "O2", "courseId": "C2", "semesterId": "TERM", "status": "OPEN",
                 "capacity": 2, "days": []},
            ],
        },
    }
    latest = copy.deepcopy(initial)
    latest["offeringCounts"]["O1"] = 2
    latest["teachingAssignments"]["O1"]["fullName"] = "New actual teacher"
    latest["choices"] = [{"offering_id": "O2", "choice_type": "PRIMARY", "selection_status": "SELECTED"}]
    mode = {"data": initial, "fail": False, "reads": 0}

    def route_api(route):
        url = route.request.url
        status = 200
        if "/api/session" in url:
            payload = {"authenticated": True, "csrfToken": "test",
                       "user": {"studentId": "S", "fullName": "Student"}}
        else:
            mode["reads"] += 1
            status = 503 if mode["fail"] else 200
            payload = {"error": "Simulated offline"} if mode["fail"] else mode["data"]
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

    with sync_playwright() as playwright:
        executable = os.environ.get("CHROME_EXECUTABLE")
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/session", route_api)
            page.route("**/api/student/data*", route_api)
            page.goto(os.environ.get("STUDENT_TEST_URL", "http://127.0.0.1:8765"))
            page.wait_for_load_state("networkidle")
            page.locator('.nav-item[data-page="register"]').click()
            card = page.locator(".course-card").filter(has_text="Advanced")
            expect(card).to_contain_text("Computer Science")
            expect(card).to_contain_text("Actual teacher")
            expect(card).to_contain_text("C2 · Basics")
            expect(card).not_to_contain_text("Wrong legacy teacher")
            card.locator('[data-pick-kind="primary"]').click()
            page.locator("#course-search").fill("Advanced")
            page.locator("#department-filter").select_option("CS")
            page.evaluate("showAlert('Existing submit error', 'error')")
            mode["data"] = latest
            # 等待真实 15 秒定时器，不直接调用刷新函数。
            expect(page.locator("#catalog-notice")).to_contain_text("已满", timeout=20000)
            expect(card).to_contain_text("New actual teacher")
            expect(page.locator("#primary-slots")).to_contain_text("Advanced")
            expect(page.locator("#primary-slots")).not_to_contain_text("Basics")
            expect(page.locator("#course-search")).to_have_value("Advanced")
            expect(page.locator("#department-filter")).to_have_value("CS")
            expect(page.locator("#page-alert")).to_contain_text("Existing submit error")
            page.locator('[data-remove-kind="primary"]').click()
            page.evaluate("refreshAvailability()")
            assert page.locator('[data-remove-kind="primary"]').count() == 0
            mode["fail"] = True
            page.evaluate("refreshAvailability()")
            expect(page.locator("#catalog-notice")).to_contain_text("Simulated offline")
            mode["fail"] = False
            closed = copy.deepcopy(latest)
            closed["canEdit"] = False
            closed["period"]["registration_status"] = "CLOSED"
            mode["data"] = closed
            page.evaluate("refreshAvailability()")
            expect(page.locator("#submit-schedule")).to_be_disabled()
            expect(page.locator("#registration-status")).to_contain_text("注册已关闭")
            page.evaluate("stopAvailabilityRefresh()")
            page.screenshot(path="/tmp/softwareexperiment-student-catalog.png", full_page=True)
            assert not errors, errors
            print(f"Browser regression passed: {mode['reads']} API reads, no page errors")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
