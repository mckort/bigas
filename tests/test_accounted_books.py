"""Weekly CFO bookkeeping block from Accounted."""
from __future__ import annotations

import unittest
from datetime import date

from bigas.resources.cto.usage.accounted_books import (
    format_accounted_books_section,
    format_company_books,
    parse_cfo_companies,
)


class ParseCfoCompaniesTests(unittest.TestCase):
    def test_reads_label_and_uuid_per_line(self):
        raw = """
        # comment
        Scaleup Advisor:11111111-1111-1111-1111-111111111111
        Green Promo Wear:22222222-2222-2222-2222-222222222222
        """
        self.assertEqual(
            parse_cfo_companies(raw),
            [
                ("Scaleup Advisor", "11111111-1111-1111-1111-111111111111"),
                ("Green Promo Wear", "22222222-2222-2222-2222-222222222222"),
            ],
        )

    def test_empty_config_is_no_companies(self):
        self.assertEqual(parse_cfo_companies(""), [])
        self.assertIsNone(format_accounted_books_section(companies_raw="", api_key="x"))


class FormatCompanyBooksTests(unittest.TestCase):
    def test_month_block_lists_costs_cash_and_open_items(self):
        text = format_company_books(
            "Scaleup Advisor",
            {
                "month_start": date(2026, 10, 1),
                "month_end": date(2026, 10, 15),
                "prev_end": date(2026, 9, 30),
                "year_start": date(2026, 1, 1),
                "current": {
                    "nettoomsattning": 12000,
                    "net_result": -3000,
                    "total_expenses": 15000,
                    "expense_sections": [
                        {
                            "title": "Övriga externa tjänster",
                            "subtotal": 15000,
                            "rows": [
                                {"account_number": "6570", "account_name": "Programvaror", "amount": 8000},
                                {"account_number": "5010", "account_name": "Lokalhyra", "amount": 7000},
                            ],
                        },
                        {
                            "title": "Inköp utlandet",
                            "subtotal": 0,
                            "rows": [
                                {"account_number": "4531", "account_name": "Import", "amount": 3000},
                                {"account_number": "4549", "account_name": "Motkonto", "amount": -3000},
                            ],
                        },
                    ],
                },
                "previous": {"total_expenses": 13000},
                "year": {
                    "nettoomsattning": 80000,
                    "net_result": 20000,
                    "total_expenses": 60000,
                    "expense_sections": [
                        {
                            "title": "Övriga externa tjänster",
                            "subtotal": 60000,
                            "rows": [
                                {"account_number": "6540", "account_name": "IT-tjänster", "amount": 40000},
                            ],
                        },
                        {
                            "title": "Inköp utlandet",
                            "subtotal": 400,
                            "rows": [
                                {"account_number": "4535", "account_name": "EU-tjänster", "amount": 5000},
                                {"account_number": "4549", "account_name": "Motkonto", "amount": -4600},
                            ],
                        },
                    ],
                },
                "receivables": 1000,
                "cash_now": 40000,
                "cash_then": 45000,
                "payables": 2000,
                "unbooked": 3,
                "inbox": 1,
            },
        )
        self.assertIn("**Scaleup Advisor**", text)
        self.assertIn("**Oktober (1–15)**", text)
        self.assertIn("**Hittills i år (1 januari–15 oktober)**", text)
        self.assertIn("Intäkter 80 000 kr · Resultat 20 000 kr", text)
        self.assertIn("Kostnader 60 000 kr", text)
        self.assertIn("- 6540 IT-tjänster: 40 000 kr", text)
        self.assertIn("- Inköp utlandet: 400 kr", text)
        self.assertNotIn("4535", text)
        self.assertIn("Intäkter 12 000 kr", text)
        self.assertIn("Resultat −3 000 kr", text)
        self.assertIn("Kostnader 15 000 kr (2 000 kr upp mot september)", text)
        self.assertIn("- 6570 Programvaror: 8 000 kr", text)
        self.assertNotIn("4531", text)
        self.assertIn("Kassa på banken 40 000 kr (5 000 kr ner mot 30 september)", text)
        self.assertIn("Kundfordringar 1 000 kr · Leverantörsskulder 2 000 kr", text)
        self.assertIn("Obokat: 3 bankrader, 1 underlag i inkorgen", text)
        self.assertNotIn("moms", text.lower())
