"""Additive SQLite schema upgrades; existing rows and tables are never reset."""

from sqlalchemy import Engine, inspect


ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "professional_profiles": {
        "employment_history": "JSON NOT NULL DEFAULT '[]'",
        "education": "JSON NOT NULL DEFAULT '[]'",
        "certifications": "JSON NOT NULL DEFAULT '[]'",
        "achievements": "JSON NOT NULL DEFAULT '[]'",
        "contact_details": "JSON NOT NULL DEFAULT '{}'",
        "nysc_completed": "BOOLEAN NOT NULL DEFAULT 1",
        "nysc_discharge_certificate": "BOOLEAN NOT NULL DEFAULT 1",
        "source_cv_id": "INTEGER",
    },
    "jobs": {
        "source_identifier": "VARCHAR(256)",
        "employment_type": "VARCHAR(80)",
        "salary_min": "FLOAT",
        "salary_max": "FLOAT",
        "salary_currency": "VARCHAR(8)",
        "required_skills": "JSON NOT NULL DEFAULT '[]'",
        "preferred_skills": "JSON NOT NULL DEFAULT '[]'",
        "experience_requirements": "TEXT",
        "updated_at": "DATETIME",
    },
    "applications": {
        "applied_at": "DATETIME",
        "interview_at": "DATETIME",
        "follow_up_at": "DATETIME",
        "recruiter_name": "VARCHAR(240)",
        "recruiter_email": "VARCHAR(320)",
        "recruiter_url": "VARCHAR(2048)",
        "updated_at": "DATETIME",
    },
}


def upgrade_schema(engine: Engine) -> None:
    """Apply version 1 using only missing-column ALTERs and a version marker."""
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        applied = {row[0] for row in connection.exec_driver_sql("SELECT version FROM schema_migrations")}
        if 1 in applied:
            return
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        for table, definitions in ADDITIVE_COLUMNS.items():
            if table not in tables:
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, definition in definitions.items():
                if name not in existing:
                    connection.exec_driver_sql(
                        f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}'
                    )
        connection.exec_driver_sql(
            "INSERT INTO schema_migrations(version) VALUES (1)"
        )
