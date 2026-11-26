import unittest

from app.services.data_service import DataService


class DataServiceTests(unittest.TestCase):
    def test_normalize_identifier(self) -> None:
        self.assertEqual(DataService.normalize_identifier("Monthly Sales!.csv", "uploaded_data"), "monthly_sales_csv")

    def test_normalize_identifier_prefixes_number(self) -> None:
        self.assertEqual(DataService.normalize_identifier("2026 Sales", "uploaded_data"), "_2026_sales")

    def test_normalize_identifier_uses_fallback_for_blank_value(self) -> None:
        self.assertEqual(DataService.normalize_identifier("!", "uploaded_data"), "uploaded_data")


class UploadTests(unittest.TestCase):
    def test_csv_upload_creates_queryable_table_in_sqlite(self):
        import os, tempfile
        from pathlib import Path
        from scripts.seed_demo_db import build
        from app.sql.executor import Database
        from app.sql.guard import check_sql

        db_path = build(Path(tempfile.mkdtemp()) / "sales.db")
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
        try:
            name, rows, cols = DataService().import_csv("Monthly Targets.csv", b"month,target\n2025-01,100\n2025-02,120\n")
            self.assertEqual((name, rows, cols), ("monthly_targets", 2, ["month", "target"]))
            df = Database().execute(check_sql("SELECT SUM(target) AS t FROM monthly_targets").limited_sql).df
            self.assertEqual(int(df.iloc[0, 0]), 220)
            with self.assertRaises(ValueError):
                DataService().import_csv("orders.csv", b"a\n1\n")  # demo tables are protected
        finally:
            os.environ.pop("DATABASE_URL", None)


if __name__ == "__main__":
    unittest.main()
