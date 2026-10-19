from jobs.constants import DEFAULT_PROVIDERS
from jobs.filters import matches_level_filter, normalize_allowed_levels
from jobs.providers.common import dedupe_offers
from jobs.providers.isitfair import collect_offers as collect_isitfair_offers
from jobs.providers.olx import collect_offers as collect_olx_offers
from jobs.providers.custom import collect_offers as collect_json_offers
from jobs.api_logging import report_provider_error, report_provider_success


def fetch_matching_offers(filters, max_pages, providers=None):
    offers = []
    for provider in DEFAULT_PROVIDERS if providers is None else providers:
        if not provider.get('enabled', True):
            continue
        kind = provider['type']
        source = filters.get('offer_source')
        if kind == 'olx' and source and source != 'olx.pl':
            continue
        if kind == 'isitfair' and source == 'olx.pl':
            continue
        try:
            collector = {'isitfair': collect_isitfair_offers, 'olx': collect_olx_offers,
                         'json': collect_json_offers}[kind]
            offers.extend(collector(filters, max_pages, provider=provider))
            if kind == 'json':
                report_provider_success(provider['name'])
        except Exception as exc:
            # Request exceptions may contain credentials from query parameters.
            report_provider_error(provider['name'], RuntimeError(type(exc).__name__))
    allowed = normalize_allowed_levels(filters.get('allowed_levels'))
    return [offer for offer in dedupe_offers(offers) if matches_level_filter(offer, allowed)]
