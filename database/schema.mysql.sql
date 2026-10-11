CREATE DATABASE IF NOT EXISTS course_registration
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE course_registration;

CREATE TABLE IF NOT EXISTS app_users (
    user_id VARCHAR(32) PRIMARY KEY,
    username VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(16) NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    CONSTRAINT chk_user_role CHECK (role IN ('STUDENT', 'PROFESSOR', 'REGISTRAR')),
    CONSTRAINT chk_user_active CHECK (is_active IN (0, 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS students (
    student_id VARCHAR(32) PRIMARY KEY,
    user_id VARCHAR(32) NOT NULL UNIQUE,
    full_name VARCHAR(100) NOT NULL,
    date_of_birth DATE NOT NULL,
    ssn_encrypted VARBINARY(512) NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    graduation_date DATE NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    CONSTRAINT chk_student_status CHECK (status IN ('ACTIVE', 'INACTIVE', 'GRADUATED')),
    CONSTRAINT fk_students_user FOREIGN KEY (user_id) REFERENCES app_users(user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS professors (
    professor_id VARCHAR(32) PRIMARY KEY,
    user_id VARCHAR(32) NOT NULL UNIQUE,
    full_name VARCHAR(100) NOT NULL,
    date_of_birth DATE NOT NULL,
    ssn_encrypted VARBINARY(512) NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    department_id VARCHAR(32) NOT NULL COMMENT 'External ID from the read-only Course Catalog API',
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    CONSTRAINT chk_professor_status CHECK (status IN ('ACTIVE', 'INACTIVE')),
    CONSTRAINT fk_professors_user FOREIGN KEY (user_id) REFERENCES app_users(user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS registrars (
    registrar_id VARCHAR(32) PRIMARY KEY,
    user_id VARCHAR(32) NOT NULL UNIQUE,
    full_name VARCHAR(100) NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    CONSTRAINT fk_registrars_user FOREIGN KEY (user_id) REFERENCES app_users(user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS registration_periods (
    semester_id VARCHAR(32) PRIMARY KEY COMMENT 'External ID from the read-only Course Catalog API',
    registration_status VARCHAR(16) NOT NULL,
    registration_start_at DATE NOT NULL,
    registration_end_at DATE NOT NULL,
    add_drop_start_at DATE NOT NULL,
    add_drop_end_at DATE NOT NULL,
    closed_at DATETIME(3) NULL,
    CONSTRAINT chk_registration_status CHECK (registration_status IN ('OPEN', 'CLOSED')),
    CONSTRAINT chk_registration_dates CHECK (registration_start_at <= registration_end_at),
    CONSTRAINT chk_add_drop_dates CHECK (add_drop_start_at <= add_drop_end_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS student_schedules (
    schedule_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    student_id VARCHAR(32) NOT NULL,
    semester_id VARCHAR(32) NOT NULL COMMENT 'External ID from the read-only Course Catalog API',
    status VARCHAR(16) NOT NULL DEFAULT 'DRAFT',
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    CONSTRAINT chk_schedule_status CHECK (status IN ('DRAFT', 'SUBMITTED', 'FINALIZED', 'CANCELLED')),
    CONSTRAINT uq_student_semester_schedule UNIQUE (student_id, semester_id),
    CONSTRAINT fk_schedules_student FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE,
    INDEX idx_schedules_student_semester (student_id, semester_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS schedule_choices (
    choice_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    schedule_id BIGINT UNSIGNED NOT NULL,
    offering_id VARCHAR(64) NOT NULL COMMENT 'External ID from the read-only Course Catalog API',
    choice_type VARCHAR(16) NOT NULL,
    preference_order TINYINT UNSIGNED NOT NULL,
    selection_status VARCHAR(16) NOT NULL DEFAULT 'SELECTED',
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    CONSTRAINT chk_choice_type CHECK (choice_type IN ('PRIMARY', 'ALTERNATE')),
    CONSTRAINT chk_choice_priority CHECK (
      (choice_type = 'PRIMARY' AND preference_order BETWEEN 1 AND 4)
      OR (choice_type = 'ALTERNATE' AND preference_order BETWEEN 1 AND 2)
    ),
    CONSTRAINT chk_choice_status CHECK (selection_status IN ('SELECTED', 'ENROLLED', 'REJECTED', 'CANCELLED')),
    CONSTRAINT uq_schedule_offering UNIQUE (schedule_id, offering_id),
    CONSTRAINT uq_schedule_choice_order UNIQUE (schedule_id, choice_type, preference_order),
    CONSTRAINT fk_choices_schedule FOREIGN KEY (schedule_id) REFERENCES student_schedules(schedule_id) ON DELETE CASCADE,
    INDEX idx_schedule_choices_schedule (schedule_id, choice_type, preference_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS professor_course_eligibility (
    professor_id VARCHAR(32) NOT NULL,
    course_id VARCHAR(32) NOT NULL COMMENT 'External ID from the read-only Course Catalog API',
    PRIMARY KEY (professor_id, course_id),
    CONSTRAINT fk_eligibility_professor FOREIGN KEY (professor_id) REFERENCES professors(professor_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS professor_offering_selections (
    professor_id VARCHAR(32) NOT NULL,
    offering_id VARCHAR(64) NOT NULL UNIQUE COMMENT 'External ID from the read-only Course Catalog API',
    semester_id VARCHAR(32) NOT NULL COMMENT 'External ID from the read-only Course Catalog API',
    selection_status VARCHAR(16) NOT NULL DEFAULT 'SELECTED',
    selected_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (professor_id, offering_id),
    CONSTRAINT chk_professor_selection_status CHECK (selection_status IN ('SELECTED', 'CONFIRMED', 'WITHDRAWN')),
    CONSTRAINT fk_selections_professor FOREIGN KEY (professor_id) REFERENCES professors(professor_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS enrollments (
    enrollment_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    schedule_id BIGINT UNSIGNED NOT NULL,
    offering_id VARCHAR(64) NOT NULL COMMENT 'External ID from the read-only Course Catalog API',
    course_id VARCHAR(32) NOT NULL COMMENT 'External ID from the read-only Course Catalog API',
    course_title_snapshot VARCHAR(200) NOT NULL,
    credits_snapshot TINYINT UNSIGNED NOT NULL,
    enrollment_status VARCHAR(16) NOT NULL DEFAULT 'ENROLLED',
    enrolled_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    CONSTRAINT chk_enrollment_credits CHECK (credits_snapshot > 0),
    CONSTRAINT chk_enrollment_status CHECK (enrollment_status IN ('ENROLLED', 'DROPPED', 'CANCELLED', 'COMPLETED')),
    CONSTRAINT uq_schedule_enrollment UNIQUE (schedule_id, offering_id),
    CONSTRAINT fk_enrollments_schedule FOREIGN KEY (schedule_id) REFERENCES student_schedules(schedule_id),
    INDEX idx_enrollments_offering (offering_id, enrollment_status),
    INDEX idx_enrollments_schedule (schedule_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS course_grades (
    enrollment_id BIGINT UNSIGNED PRIMARY KEY,
    professor_id VARCHAR(32) NOT NULL,
    letter_grade CHAR(1) NOT NULL,
    graded_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    CONSTRAINT chk_letter_grade CHECK (letter_grade IN ('A', 'B', 'C', 'D', 'F', 'I')),
    CONSTRAINT fk_grades_enrollment FOREIGN KEY (enrollment_id) REFERENCES enrollments(enrollment_id) ON DELETE CASCADE,
    CONSTRAINT fk_grades_professor FOREIGN KEY (professor_id) REFERENCES professors(professor_id),
    INDEX idx_grades_professor (professor_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS billing_transactions (
    transaction_id VARCHAR(64) PRIMARY KEY,
    student_id VARCHAR(32) NOT NULL,
    semester_id VARCHAR(32) NOT NULL COMMENT 'External ID from the read-only Course Catalog API',
    idempotency_key VARCHAR(128) NOT NULL UNIQUE,
    amount_cents BIGINT UNSIGNED NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    payload_json JSON NOT NULL,
    attempt_count INT UNSIGNED NOT NULL DEFAULT 0,
    last_error TEXT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    CONSTRAINT chk_billing_status CHECK (status IN ('PENDING', 'SENT', 'RETRY', 'FAILED')),
    CONSTRAINT fk_billing_student FOREIGN KEY (student_id) REFERENCES students(student_id),
    INDEX idx_billing_status (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS audit_logs (
    audit_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    actor_user_id VARCHAR(32) NULL,
    action VARCHAR(64) NOT NULL,
    entity_type VARCHAR(64) NOT NULL,
    entity_id VARCHAR(128) NOT NULL,
    details_json JSON NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    CONSTRAINT fk_audit_actor FOREIGN KEY (actor_user_id) REFERENCES app_users(user_id),
    INDEX idx_audit_actor_time (actor_user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TRIGGER IF EXISTS grades_require_assigned_professor_insert;
DROP TRIGGER IF EXISTS grades_require_assigned_professor_update;
DELIMITER $$
CREATE TRIGGER grades_require_assigned_professor_insert
BEFORE INSERT ON course_grades
FOR EACH ROW
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM enrollments e
        JOIN professor_offering_selections p ON p.offering_id = e.offering_id
        WHERE e.enrollment_id = NEW.enrollment_id
          AND p.professor_id = NEW.professor_id
          AND p.selection_status IN ('SELECTED', 'CONFIRMED')
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Professor is not assigned to this course offering';
    END IF;
END$$
CREATE TRIGGER grades_require_assigned_professor_update
BEFORE UPDATE ON course_grades
FOR EACH ROW
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM enrollments e
        JOIN professor_offering_selections p ON p.offering_id = e.offering_id
        WHERE e.enrollment_id = NEW.enrollment_id
          AND p.professor_id = NEW.professor_id
          AND p.selection_status IN ('SELECTED', 'CONFIRMED')
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Professor is not assigned to this course offering';
    END IF;
END$$
DELIMITER ;

CREATE OR REPLACE VIEW v_student_report_cards AS
SELECT s.student_id, s.full_name AS student_name, sch.semester_id,
       e.offering_id, e.course_id, e.course_title_snapshot, e.credits_snapshot,
       g.letter_grade, g.professor_id, g.graded_at
FROM student_schedules sch
JOIN students s ON s.student_id = sch.student_id
JOIN enrollments e ON e.schedule_id = sch.schedule_id
JOIN course_grades g ON g.enrollment_id = e.enrollment_id
WHERE e.enrollment_status = 'COMPLETED';

CREATE OR REPLACE VIEW v_course_rosters AS
SELECT e.offering_id, sch.semester_id, s.student_id, s.full_name AS student_name,
       e.enrollment_status, e.enrolled_at
FROM enrollments e
JOIN student_schedules sch ON sch.schedule_id = e.schedule_id
JOIN students s ON s.student_id = sch.student_id
WHERE e.enrollment_status = 'ENROLLED';
