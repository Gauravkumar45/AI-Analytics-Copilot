import unittest

from app.services.data_service import DataService


class DataServiceTests(unittest.TestCase):
    def test_normalize_identifier(self) -> None:
        self.assertEqual(DataService.normalize_identifier("Monthly Sales!.csv", "uploaded_data"), "monthly_sales_csv")

    def test_normalize_identifier_prefixes_number(self) -> None:
        self.assertEqual(DataService.normalize_identifier("2026 Sales", "uploaded_data"), "_2026_sales")

    def test_normalize_identifier_uses_fallback_for_blank_value(self) -> None:
        self.assertEqual(DataService.normalize_identifier("!", "uploaded_data"), "uploaded_data")


if __name__ == "__main__":
    unittest.main()
