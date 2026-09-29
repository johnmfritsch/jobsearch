"""Fixture-backed regressions for priority-employer scraping and provenance."""
import pathlib
import unittest
from unittest import mock

import safe_fetch
from deduplicate_jobs import deduplicate_jobs
from scrapers import adapter_registry
from scrapers import employer_source_scraper
from scrapers import successfactors_scraper as sf


FIXTURES = pathlib.Path(__file__).with_name('fixtures')


def _page(name, url):
    return safe_fetch.FetchResult(url, 200, 'text/html',
                                  (FIXTURES / name).read_text(encoding='utf-8'),
                                  {'content-type': 'text/html'}, '93.184.216.34')


class SuccessFactorsFixtureTests(unittest.TestCase):
    source = {'company_name': 'Landis+Gyr'}
    board_url = 'https://careers.example.com/go/View-All-Jobs/0/'

    def test_html_pagination_collects_a_bounded_second_page_and_reports_total(self):
        def fake_fetch(url, _host, **_kwargs):
            if '/25/' in url:
                return _page('successfactors_list_page_2.html', url)
            return _page('successfactors_list_page_1.html', url)

        # The minimized fixture contains only two visible jobs, while a real
        # SF page has 25.  Exercise collection with the actual page-two URL,
        # then independently verify the 25-result offset convention below.
        with mock.patch.object(sf, '_html_next_page_urls', return_value=[
                'https://careers.example.com/go/View-All-Jobs/25/']), \
                mock.patch.object(sf, '_fetch', side_effect=fake_fetch):
            listings, _html, total = sf._html_listings(self.board_url, self.source, 'engineer', max_pages=3)
        self.assertEqual(total, 3)
        self.assertEqual([item['title'] for item in listings],
                         ['Platform Engineer', 'Data Engineer', 'Integration Engineer'])
        self.assertTrue(all(item['keywords'] == ['engineer'] for item in listings))
        next_urls = sf._html_next_page_urls(
            (FIXTURES / 'successfactors_list_page_1.html').read_text(encoding='utf-8'),
            self.board_url, page_size=25, max_pages=3)
        self.assertEqual(next_urls, ['https://careers.example.com/go/View-All-Jobs/25/'])

    def test_detail_keeps_listing_requisition_when_url_has_no_numeric_id(self):
        listing = {'id': 'listing-42', 'title': 'Platform Engineer',
                   'url': 'https://careers.example.com/job/Platform-Engineer'}
        with mock.patch.object(sf, '_fetch', return_value=_page(
                'successfactors_detail.html', listing['url'])):
            job = sf._detail_job(listing, self.source, 'careers.example.com')
        self.assertEqual(job['id'], 'successfactors:careers.example.com:listing-42')
        self.assertEqual(job['location'], 'Madison, WI')
        self.assertEqual(job['employment_type'], 'Hybrid')
        self.assertEqual(job['apply_url'], 'https://careers.example.com/apply/111')
        self.assertTrue(job['_employer_direct'])
        self.assertIn('Build resilient integrations', job['full_description'])


class EmployerDispatchAndDeduplicationTests(unittest.TestCase):
    def test_adapter_registry_distinguishes_direct_fallback_and_unsupported_urls(self):
        self.assertEqual(adapter_registry.classify_url('https://jobs.example.com/go/Jobs/1/'), 'successfactors')
        self.assertEqual(adapter_registry.classify_url('https://acme.wd1.myworkdayjobs.com/Jobs'), 'workday')
        self.assertEqual(adapter_registry.classify_url(
            'https://recruiting2.ultipro.com/ACME/JobBoard/12345678-1234-1234-1234-123456789abc'), 'ukg')
        self.assertEqual(adapter_registry.classify_url('https://boards.greenhouse.io/acme'), 'unsupported')
        self.assertEqual(adapter_registry.classify_url(''), 'serpapi_company')

    def test_fallback_test_never_scrapes_the_saved_unsupported_url(self):
        source = {'adapter': 'unsupported', 'company_name': 'Acme',
                  'careers_url': 'https://boards.greenhouse.io/acme'}
        result = employer_source_scraper.test_employer_source(source, deadline=0)
        self.assertEqual(result['count'], 0)
        self.assertIn('isn\'t a job-board platform', result['message'])

    def test_direct_employer_record_wins_fields_and_preserves_aggregator_provenance(self):
        aggregator = {
            'title': 'Platform Engineer', 'company': 'Landis Gyr',
            'url': 'https://aggregator.example/job', 'description': 'short',
            'source': 'SerpAPI', '_sources': ['SerpAPI'],
            '_search_keywords': ['engineer'], '_search_modes': ['remote'],
        }
        direct = {
            'title': 'Platform Engineer', 'company': 'Landis Gyr',
            'url': 'https://careers.example/job/42', 'description': 'canonical description',
            'source': 'Landis+Gyr careers', '_sources': ['Landis+Gyr careers'],
            '_search_keywords': ['engineer'], '_search_modes': ['employer'],
            '_target_employers': ['Landis+Gyr'], '_employer_direct': True,
        }
        result = deduplicate_jobs([aggregator, direct])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['url'], direct['url'])
        self.assertEqual(result[0]['description'], 'canonical description')
        self.assertEqual(result[0]['_sources'], ['SerpAPI', 'Landis+Gyr careers'])
        self.assertEqual(result[0]['_target_employers'], ['Landis+Gyr'])
