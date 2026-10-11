"""原学生页面的成绩学期筛选与未保存课表保留的 Playwright 回归。"""

import json
import os
from urllib.parse import parse_qs, urlparse


def main():
    from playwright.sync_api import sync_playwright, expect

    terms = [{"semesterId": "RECENT", "name": "上一学期"},
             {"semesterId": "OLD", "name": "历史学期"}, {"semesterId": "EMPTY", "name": "空成绩学期"}]

    def transcript(term):
        return {
            "transcriptSemesterId": term, "transcriptSemesters": terms, "completedCourseCount": 2,
            "transcript": [] if term == "EMPTY" else [{
                "semester_id": term, "course_id": term, "course_title_snapshot": f"{term} course",
                "credits_snapshot": 3, "letter_grade": "A" if term == "RECENT" else "F", "professor_id": "P",
            }],
        }

    data = {
        "student": {"student_id": "S", "full_name": "Student"},
        "semesterId": "CURRENT", "semesters": [{"semesterId": "CURRENT", "name": "当前学期"}],
        "period": {"registration_status": "OPEN"}, "schedule": {"status": "DRAFT"},
        "canEdit": True, "registrationOpen": True, "addDropOpen": True, "previouslySubmitted": False,
        "enrollments": [], "choices": [], "offeringCounts": {}, "teachingAssignments": {"O": {"fullName": "Teacher"}},
        "catalog": {
            "courses": [{"courseId": "C", "title": "Unsaved course", "departmentId": "CS", "credits": 3}],
            "departments": [{"departmentId": "CS", "name": "Computer Science"}], "faculty": [], "prerequisites": [],
            "courseOfferings": [{"offeringId": "O", "courseId": "C", "semesterId": "CURRENT", "status": "OPEN",
                                 "capacity": 20, "days": []}],
        },
    }
    reads = []

    def route_api(route):
        url = urlparse(route.request.url)
        query = parse_qs(url.query)
        reads.append(url.path)
        if url.path == "/api/session":
            payload = {"authenticated": True, "csrfToken": "test", "user": {"studentId": "S", "fullName": "Student"}}
        elif url.path == "/api/student/transcript":
            payload = transcript(query.get("semester", ["RECENT"])[0])
        else:
            payload = {**data, **transcript(query.get("transcriptSemester", ["RECENT"])[0])}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    with sync_playwright() as playwright:
        executable = os.environ.get("CHROME_EXECUTABLE")
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/**", route_api)
            page.goto(os.environ.get("STUDENT_TEST_URL", "http://127.0.0.1:8765"))
            page.wait_for_load_state("networkidle")
            page.locator('.nav-item[data-page="register"]').click()
            page.locator('[data-pick-kind="primary"]').click()
            page.locator("#course-search").fill("Unsaved")
            page.locator("#department-filter").select_option("CS")
            page.locator('.nav-item[data-page="transcript"]').click()
            expect(page.locator("#semester-select")).to_have_value("RECENT")
            expect(page.locator("#transcript-rows")).to_contain_text("RECENT course")
            page.locator("#transcript-semester-select").select_option("OLD")
            expect(page.locator("#transcript-rows")).to_contain_text("OLD course")
            expect(page.locator("#transcript-rows")).not_to_contain_text("RECENT course")
            expect(page.locator("#semester-select")).to_have_value("OLD")
            # 自动刷新继续请求当前成绩学期，同时保留选课草稿。
            page.evaluate("refreshAvailability()")
            expect(page.locator("#transcript-semester-select")).to_have_value("OLD")
            page.locator("#semester-select").select_option("EMPTY")
            expect(page.locator("#transcript-empty")).to_be_visible()
            expect(page.locator("#transcript-empty-copy")).to_contain_text("空成绩学期暂未录入成绩")
            page.locator("#semester-select").select_option("RECENT")
            expect(page.locator("#transcript-rows")).to_contain_text("RECENT course")
            expect(page.locator("#metric-completed")).to_have_text("2")
            page.locator('.nav-item[data-page="register"]').click()
            expect(page.locator("#semester-select")).to_have_value("CURRENT")
            expect(page.locator("#primary-slots")).to_contain_text("Unsaved course")
            expect(page.locator("#course-search")).to_have_value("Unsaved")
            expect(page.locator("#department-filter")).to_have_value("CS")
            page.locator('.nav-item[data-page="transcript"]').click()
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.locator("#transcript-semester-select")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.evaluate("stopAvailabilityRefresh()")
            page.screenshot(path="/tmp/softwareexperiment-student-transcript.png", full_page=True)
            assert "/api/student/transcript" in reads
            assert not errors, errors
            print("Browser transcript regression passed: both filters, empty term, polling, draft preservation, mobile, no page errors")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
