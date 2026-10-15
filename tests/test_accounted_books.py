"""Weekly CFO bookkeeping block from Accounted."""
from __future__ import annotations

import os
import unittest
from datetime import date
from unittest.mock import patch

import requests

from bigas.resources.cto.usage.accounted_books import (
    _Client,
    _count_list,
    _period_bounds,
    _windows,
    fetch_company_books,
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

    def test_malformed_subtotal_does_not_drop_the_company(self):
        text = format_company_books(
            "Acme",
            {
                "month_start": date(2026, 10, 1),
                "month_end": date(2026, 10, 15),
                "prev_end": date(2026, 9, 30),
                "current": {
                    "nettoomsattning": 1,
                    "net_result": 1,
                    "total_expenses": 1,
                    "expense_sections": [
                        {
                            "title": "Trasigt",
                            "subtotal": "not-a-number",
                            "rows": [
                                {
                                    "account_number": "6570",
                                    "account_name": "Programvaror",
                                    "amount": 10,
                                }
                            ],
                        }
                    ],
                },
                "previous": {"total_expenses": "nope"},
                "cash_now": 1,
                "cash_then": 1,
                "receivables": 0,
                "payables": 0,
                "unbooked": 0,
                "inbox": 0,
            },
        )
        self.assertIn("**Acme**", text)
        self.assertIn("Inga kostnadskonton den här månaden", text)


class PeriodBoundsTests(unittest.TestCase):
    def test_timestamped_bounds_include_the_first_day(self):
        bounds = _period_bounds(
            [
                {
                    "id": "fy",
                    "period_start": "2026-01-01T00:00:00Z",
                    "period_end": "2026-12-31T23:59:59Z",
                }
            ],
            date(2026, 1, 1),
        )
        self.assertEqual(bounds, ("fy", date(2026, 1, 1), date(2026, 12, 31)))

    def test_invalid_period_does_not_hide_a_later_match(self):
        bounds = _period_bounds(
            [
                {
                    "id": "",
                    "period_start": "2026-01-01T00:00:00Z",
                    "period_end": "2026-12-31",
                },
                {
                    "id": "bad-date",
                    "period_start": "2026-02-30",
                    "period_end": "2026-12-31",
                },
                {
                    "id": "fy",
                    "period_start": "2026-01-01",
                    "period_end": "2026-12-31",
                },
            ],
            date(2026, 10, 15),
        )
        self.assertEqual(bounds[0], "fy")


class WindowTests(unittest.TestCase):
    def test_mid_month_is_month_to_date(self):
        self.assertEqual(
            _windows(date(2026, 10, 15)),
            (date(2026, 10, 1), date(2026, 10, 15), date(2026, 9, 1), date(2026, 9, 30)),
        )

    def test_first_of_month_uses_the_completed_prior_month(self):
        self.assertEqual(
            _windows(date(2026, 3, 1)),
            (date(2026, 2, 1), date(2026, 2, 28), date(2026, 1, 1), date(2026, 1, 31)),
        )

    def test_new_year_uses_december(self):
        self.assertEqual(
            _windows(date(2026, 1, 1)),
            (date(2025, 12, 1), date(2025, 12, 31), date(2025, 11, 1), date(2025, 11, 30)),
        )


class _JsonResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        return self._payload


class ClientGetTests(unittest.TestCase):
    def test_dict_data_keeps_fields_and_cursor(self):
        def fake_get(url, params=None, headers=None, timeout=None):
            return _JsonResponse(
                {"data": {"fiscal_periods": [{"id": "p"}]}, "meta": {"next_cursor": "n"}}
            )

        with patch("bigas.resources.cto.usage.accounted_books.requests.get", side_effect=fake_get):
            body = _Client("k").get("/companies/c/fiscal-periods")
        self.assertEqual(body, {"fiscal_periods": [{"id": "p"}], "next_cursor": "n"})

    def test_list_data_is_wrapped_as_items(self):
        def fake_get(url, params=None, headers=None, timeout=None):
            return _JsonResponse({"data": [{"id": 1}], "meta": {"next_cursor": "n"}})

        with patch("bigas.resources.cto.usage.accounted_books.requests.get", side_effect=fake_get):
            body = _Client("k").get("/x")
        self.assertEqual(body, {"items": [{"id": 1}], "next_cursor": "n"})

    def test_api_base_override(self):
        seen = {}

        def fake_get(url, params=None, headers=None, timeout=None):
            seen["url"] = url
            return _JsonResponse({"data": {}})

        with patch.dict(os.environ, {"ACCOUNTED_API_BASE": "https://staging.example/api/v1/"}):
            with patch(
                "bigas.resources.cto.usage.accounted_books.requests.get",
                side_effect=fake_get,
            ):
                _Client("k").get("/companies/c/fiscal-periods")
        self.assertEqual(seen["url"], "https://staging.example/api/v1/companies/c/fiscal-periods")


class CountListTests(unittest.TestCase):
    def test_named_list_falls_back_to_items(self):
        class _Fake:
            def get(self, path, params=None):
                return {"items": [{}, {}]}

        self.assertEqual(_count_list(_Fake(), "/inbox", {}, list_key="inbox_items"), 2)

    def test_named_list_wins_when_present(self):
        class _Fake:
            def get(self, path, params=None):
                return {"inbox_items": [{}], "items": [{}, {}]}

        self.assertEqual(_count_list(_Fake(), "/inbox", {}, list_key="inbox_items"), 1)

    def test_pagination_stops_after_five_pages(self):
        class _Fake:
            def __init__(self):
                self.calls = 0

            def get(self, path, params=None):
                self.calls += 1
                return {"items": [{}] * 100, "next_cursor": "more"}

        client = _Fake()
        self.assertEqual(_count_list(client, "/tx", {}, list_key=None), 500)
        self.assertEqual(client.calls, 5)


def _snap():
    return {
        "month_start": date(2026, 10, 1),
        "month_end": date(2026, 10, 15),
        "prev_end": date(2026, 9, 30),
        "current": {"nettoomsattning": 10, "net_result": 1, "total_expenses": 9},
        "previous": {},
        "cash_now": 1,
        "cash_then": None,
        "receivables": 0,
        "payables": 0,
        "unbooked": 0,
        "inbox": 0,
    }


class SectionErrorTests(unittest.TestCase):
    def test_missing_key_on_the_first_names_the_prior_month(self):
        text = format_accounted_books_section(
            today=date(2026, 3, 1),
            api_key="",
            companies_raw="Acme:abc",
        )
        self.assertIn("1–28 februari 2026", text)
        self.assertIn("ACCOUNTED_API_KEY saknas.", text)

    def test_forbidden_company_is_reported(self):
        response = requests.Response()
        response.status_code = 403
        error = requests.HTTPError(response=response)

        with patch(
            "bigas.resources.cto.usage.accounted_books.fetch_company_books",
            side_effect=error,
        ):
            text = format_accounted_books_section(
                today=date(2026, 10, 15),
                api_key="k",
                companies_raw="Acme:abc",
            )
        self.assertIn("**Acme**", text)
        self.assertIn("API-nyckeln når inte det här bolaget.", text)

    def test_other_failures_keep_the_section(self):
        with patch(
            "bigas.resources.cto.usage.accounted_books.fetch_company_books",
            side_effect=requests.HTTPError(response=_status(500)),
        ):
            http = format_accounted_books_section(
                today=date(2026, 10, 15),
                api_key="k",
                companies_raw="Acme:abc",
            )
        with patch(
            "bigas.resources.cto.usage.accounted_books.fetch_company_books",
            side_effect=RuntimeError("boom"),
        ):
            other = format_accounted_books_section(
                today=date(2026, 10, 15),
                api_key="k",
                companies_raw="Acme:abc",
            )
        self.assertIn("Kunde inte läsa bokföringen.", http)
        self.assertIn("Kunde inte läsa bokföringen.", other)

    def test_companies_stay_in_config_order_when_one_fails(self):
        def fake(company_id, *, api_key, today):
            if company_id == "second":
                raise RuntimeError("boom")
            return _snap()

        with patch(
            "bigas.resources.cto.usage.accounted_books.fetch_company_books",
            side_effect=fake,
        ):
            text = format_accounted_books_section(
                today=date(2026, 10, 15),
                api_key="k",
                companies_raw="First:first\nSecond:second",
            )
        self.assertLess(text.index("**First**"), text.index("**Second**"))
        self.assertIn("Kunde inte läsa bokföringen.", text)


def _status(code):
    response = requests.Response()
    response.status_code = code
    return response


class FetchCompanyBooksTests(unittest.TestCase):
    def test_items_envelope_timestamps_and_bad_amounts(self):
        company = "11111111-1111-1111-1111-111111111111"
        seen = []

        def fake_get(url, params=None, headers=None, timeout=None):
            seen.append((url, params or {}))
            if url.endswith("/fiscal-periods"):
                return _JsonResponse(
                    {
                        "data": [
                            {
                                "id": "",
                                "period_start": "2026-01-01T00:00:00Z",
                                "period_end": "2026-12-31T00:00:00Z",
                            },
                            {
                                "id": "bad-date",
                                "period_start": "2026-02-30",
                                "period_end": "2026-12-31",
                            },
                            {
                                "id": "fy",
                                "period_start": "2026-01-01T00:00:00Z",
                                "period_end": "2026-12-31T23:59:59Z",
                            },
                        ]
                    }
                )
            if url.endswith("/reports/balance-sheet"):
                return _JsonResponse(
                    {
                        "data": {
                            "asset_sections": [
                                {
                                    "rows": [
                                        {"account_number": "1930", "amount": "bad"},
                                        {"account_number": "1930", "amount": 100},
                                    ]
                                }
                            ]
                        }
                    }
                )
            if url.endswith("/transactions"):
                return _JsonResponse({"data": [{}, {}]})
            if url.endswith("/inbox-items"):
                return _JsonResponse({"data": [{}, {}, {}]})
            if url.endswith("/reports/income-statement"):
                return _JsonResponse(
                    {
                        "data": {
                            "nettoomsattning": 1,
                            "net_result": 1,
                            "total_expenses": 1,
                            "expense_sections": [
                                {"title": "X", "subtotal": "nope", "rows": []}
                            ],
                        }
                    }
                )
            return _JsonResponse({"data": {}})

        with patch(
            "bigas.resources.cto.usage.accounted_books.requests.get",
            side_effect=fake_get,
        ):
            snap = fetch_company_books(company, api_key="k", today=date(2026, 10, 15))
        self.assertEqual(snap["year_start"], date(2026, 1, 1))
        self.assertEqual(snap["cash_now"], 100.0)
        self.assertEqual(snap["unbooked"], 2)
        self.assertEqual(snap["inbox"], 3)
        text = format_company_books("Acme", snap)
        self.assertIn("**Acme**", text)

    def test_first_of_month_queries_the_completed_period(self):
        seen = []

        def fake_get(url, params=None, headers=None, timeout=None):
            seen.append((url, dict(params or {})))
            if url.endswith("/fiscal-periods"):
                return _JsonResponse(
                    {
                        "data": {
                            "fiscal_periods": [
                                {
                                    "id": "jan",
                                    "period_start": "2026-01-01",
                                    "period_end": "2026-01-31",
                                },
                                {
                                    "id": "feb",
                                    "period_start": "2026-02-01T00:00:00Z",
                                    "period_end": "2026-02-28T00:00:00Z",
                                },
                                {
                                    "id": "mar",
                                    "period_start": "2026-03-01T00:00:00Z",
                                    "period_end": "2026-03-31T00:00:00Z",
                                },
                            ]
                        }
                    }
                )
            return _JsonResponse({"data": {}})

        with patch(
            "bigas.resources.cto.usage.accounted_books.requests.get",
            side_effect=fake_get,
        ):
            fetch_company_books("c", api_key="k", today=date(2026, 3, 1))
        income = [params for url, params in seen if url.endswith("/income-statement")]
        self.assertGreaterEqual(len(income), 2)
        self.assertEqual(income[0]["period_id"], "feb")
        self.assertEqual(income[0]["from_date"], "2026-02-01")
        self.assertEqual(income[0]["to_date"], "2026-02-28")
        self.assertEqual(income[1]["period_id"], "jan")
        self.assertEqual(income[1]["from_date"], "2026-01-01")
        self.assertEqual(income[1]["to_date"], "2026-01-31")

    def test_transport_error_becomes_a_company_line(self):
        class _Denied:
            status_code = 404

            def raise_for_status(self):
                raise requests.HTTPError(response=self)

            def json(self):
                return {}

        with patch(
            "bigas.resources.cto.usage.accounted_books.requests.get",
            return_value=_Denied(),
        ):
            text = format_accounted_books_section(
                today=date(2026, 10, 15),
                api_key="k",
                companies_raw="Acme:abc",
            )
        self.assertIn("API-nyckeln når inte det här bolaget.", text)
