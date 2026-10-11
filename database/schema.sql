PRAGMA foreign_keys = ON;

-- Users and roles for the new Course Registration System.
CREATE TABLE IF NOT EXISTS app_users (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('STUDENT', 'PROFESSOR', 'REGISTRAR')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS students (
    student_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE REFERENCES app_users(user_id),
    full_name TEXT NOT NULL,
    date_of_birth TEXT NOT NULL,
    ssn_encrypted BLOB,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE', 'GRADUATED')),
    graduation_date TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS professors (
    professor_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE REFERENCES app_users(user_id),
    full_name TEXT NOT NULL,
    date_of_birth TEXT NOT NULL,
    ssn_encrypted BLOB,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE')),
    department_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS registrars (
    registrar_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE REFERENCES app_users(user_id),
    full_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- semester_id is owned by the read-only Course Catalog System.
CREATE TABLE IF NOT EXISTS registration_periods (
    semester_id TEXT PRIMARY KEY,
    registration_status TEXT NOT NULL CHECK (registration_status IN ('OPEN', 'CLOSED')),
    registration_start_at TEXT NOT NULL,
    registration_end_at TEXT NOT NULL,
    add_drop_start_at TEXT NOT NULL,
    add_drop_end_at TEXT NOT NULL,
    closed_at TEXT,
    CHECK (registration_start_at <= registration_end_at),
    CHECK (add_drop_start_at <= add_drop_end_at)
);

CREATE TABLE IF NOT EXISTS student_schedules (
    schedule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL REFERENCES students(student_id),
    semester_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'SUBMITTED', 'FINALIZED', 'CANCELLED')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (student_id, semester_id)
);

-- offering_id values are references to the read-only Course Catalog API.
CREATE TABLE IF NOT EXISTS schedule_choices (
    choice_id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id INTEGER NOT NULL REFERENCES student_schedules(schedule_id) ON DELETE CASCADE,
    offering_id TEXT NOT NULL,
    choice_type TEXT NOT NULL CHECK (choice_type IN ('PRIMARY', 'ALTERNATE')),
    preference_order INTEGER NOT NULL,
    selection_status TEXT NOT NULL DEFAULT 'SELECTED' CHECK (selection_status IN ('SELECTED', 'ENROLLED', 'REJECTED', 'CANCELLED')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (
        (choice_type = 'PRIMARY' AND preference_order BETWEEN 1 AND 4)
        OR (choice_type = 'ALTERNATE' AND preference_order BETWEEN 1 AND 2)
    ),
    UNIQUE (schedule_id, offering_id),
    UNIQUE (schedule_id, choice_type, preference_order)
);

-- A local identity/profile is linked to course ids from the external catalog.
CREATE TABLE IF NOT EXISTS professor_course_eligibility (
    professor_id TEXT NOT NULL REFERENCES professors(professor_id),
    course_id TEXT NOT NULL,
    PRIMARY KEY (professor_id, course_id)
);

CREATE TABLE IF NOT EXISTS professor_offering_selections (
    professor_id TEXT NOT NULL REFERENCES professors(professor_id),
    offering_id TEXT NOT NULL UNIQUE,
    semester_id TEXT NOT NULL,
    selection_status TEXT NOT NULL DEFAULT 'SELECTED' CHECK (selection_status IN ('SELECTED', 'CONFIRMED', 'WITHDRAWN')),
    selected_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (professor_id, offering_id)
);

-- Course names and credits are snapshots taken at enrollment time so old
-- report cards remain readable if the external catalog later changes.
CREATE TABLE IF NOT EXISTS enrollments (
    enrollment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id INTEGER NOT NULL REFERENCES student_schedules(schedule_id),
    offering_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    course_title_snapshot TEXT NOT NULL,
    credits_snapshot INTEGER NOT NULL CHECK (credits_snapshot > 0),
    enrollment_status TEXT NOT NULL DEFAULT 'ENROLLED' CHECK (enrollment_status IN ('ENROLLED', 'DROPPED', 'CANCELLED', 'COMPLETED')),
    enrolled_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (schedule_id, offering_id)
);

CREATE TABLE IF NOT EXISTS course_grades (
    enrollment_id INTEGER PRIMARY KEY REFERENCES enrollments(enrollment_id) ON DELETE CASCADE,
    professor_id TEXT NOT NULL REFERENCES professors(professor_id),
    letter_grade TEXT NOT NULL CHECK (letter_grade IN ('A', 'B', 'C', 'D', 'F', 'I')),
    graded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- The trigger prevents a professor from grading an offering assigned to another professor.
CREATE TRIGGER IF NOT EXISTS grades_require_assigned_professor_insert
BEFORE INSERT ON course_grades
WHEN NOT EXISTS (
    SELECT 1
    FROM enrollments e
    JOIN professor_offering_selections p ON p.offering_id = e.offering_id
    WHERE e.enrollment_id = NEW.enrollment_id
      AND p.professor_id = NEW.professor_id
      AND p.selection_status IN ('SELECTED', 'CONFIRMED')
)
BEGIN
    SELECT RAISE(ABORT, 'professor is not assigned to this course offering');
END;

CREATE TRIGGER IF NOT EXISTS grades_require_assigned_professor_update
BEFORE UPDATE OF enrollment_id, professor_id ON course_grades
WHEN NOT EXISTS (
    SELECT 1
    FROM enrollments e
    JOIN professor_offering_selections p ON p.offering_id = e.offering_id
    WHERE e.enrollment_id = NEW.enrollment_id
      AND p.professor_id = NEW.professor_id
      AND p.selection_status IN ('SELECTED', 'CONFIRMED')
)
BEGIN
    SELECT RAISE(ABORT, 'professor is not assigned to this course offering');
END;

-- Billing outbox records allow safe retry when the separate billing system is down.
CREATE TABLE IF NOT EXISTS billing_transactions (
    transaction_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL REFERENCES students(student_id),
    semester_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'SENT', 'RETRY', 'FAILED')),
    payload_json TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS audit_logs (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id TEXT REFERENCES app_users(user_id),
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_schedules_student_semester ON student_schedules(student_id, semester_id);
CREATE INDEX IF NOT EXISTS idx_schedule_choices_schedule ON schedule_choices(schedule_id, choice_type, preference_order);
CREATE INDEX IF NOT EXISTS idx_enrollments_offering ON enrollments(offering_id, enrollment_status);
CREATE INDEX IF NOT EXISTS idx_enrollments_schedule ON enrollments(schedule_id);
CREATE INDEX IF NOT EXISTS idx_grades_professor ON course_grades(professor_id);
CREATE INDEX IF NOT EXISTS idx_billing_status ON billing_transactions(status, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_actor_time ON audit_logs(actor_user_id, created_at);

CREATE VIEW IF NOT EXISTS v_student_report_cards AS
SELECT s.student_id, s.full_name AS student_name, sch.semester_id,
       e.offering_id, e.course_id, e.course_title_snapshot, e.credits_snapshot,
       g.letter_grade, g.professor_id, g.graded_at
FROM student_schedules sch
JOIN students s ON s.student_id = sch.student_id
JOIN enrollments e ON e.schedule_id = sch.schedule_id
JOIN course_grades g ON g.enrollment_id = e.enrollment_id
WHERE e.enrollment_status = 'COMPLETED';

CREATE VIEW IF NOT EXISTS v_course_rosters AS
SELECT e.offering_id, sch.semester_id, s.student_id, s.full_name AS student_name,
       e.enrollment_status, e.enrolled_at
FROM enrollments e
JOIN student_schedules sch ON sch.schedule_id = e.schedule_id
JOIN students s ON s.student_id = sch.student_id
WHERE e.enrollment_status = 'ENROLLED';
