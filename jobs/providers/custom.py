"""Configurable GET JSON feeds normalized to the shared offer schema."""
from urllib.parse import urlsplit

import requests

from jobs.filters import matches_city, is_fully_remote


def path_value(value, path, default=None):
    if not path:
        return value
    for part in path.split('.'):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def validate_provider(provider):
    if provider['type'] not in ('isitfair', 'olx', 'json'):
        raise ValueError('Typ API: isitfair, olx lub json.')
    url = urlsplit(provider['url'])
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError('Podaj adres HTTPS bez hasła i fragmentu.')
    options = provider.get('options', {})
    if not isinstance(options, dict):
        raise ValueError('Opcje muszą być obiektem JSON.')
    for key in ('params', 'headers', 'fields'):
        if not isinstance(options.get(key, {}), dict):
            raise ValueError(f'{key} musi być obiektem JSON.')
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in options.get('headers', {}).items()):
        raise ValueError('Nazwy i wartości nagłówków muszą być tekstem.')
    for key in ('data_path', 'page_param'):
        if not isinstance(options.get(key, ''), str):
            raise ValueError(f'{key} musi być tekstem.')
    if any(not isinstance(v, str) for v in options.get('fields', {}).values()):
        raise ValueError('Mapowanie pól musi zawierać ścieżki tekstowe.')
    if provider['type'] == 'json':
        fields = options.get('fields', {})
        if not all(key in fields for key in ('offer_uuid', 'offer_title', 'offer_href')):
            raise ValueError('Mapowanie fields wymaga offer_uuid, offer_title i offer_href.')


def fetch_page(provider, page):
    options = provider.get('options', {})
    params = dict(options.get('params', {}))
    if options.get('page_param'):
        params[options['page_param']] = page
    response = requests.get(provider['url'], params=params,
                            headers=options.get('headers', {}), timeout=20)
    response.raise_for_status()
    data = path_value(response.json(), options.get('data_path', 'data'))
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise ValueError('Ścieżka data_path nie wskazuje listy obiektów ofert.')
    return data


def collect_offers(filters, max_pages, provider):
    options = provider.get('options', {})
    collected = []
    for page in range(1, max_pages + 1):
        items = fetch_page(provider, page)
        for item in items:
            offer = {key: path_value(item, path) for key, path in options['fields'].items()}
            if not all(offer.get(key) for key in ('offer_uuid', 'offer_title', 'offer_href')):
                continue
            offer['offer_uuid'] = f"{provider['id']}:{offer['offer_uuid']}"
            offer.setdefault('offer_source', provider['name'])
            offer.setdefault('company', {})
            if not isinstance(offer['company'], dict):
                offer['company'] = {'company_name': str(offer['company'] or '')}
            for key in ('offer_title', 'offer_href', 'offer_city', 'offer_category', 'offer_source'):
                if offer.get(key) is not None:
                    offer[key] = str(offer[key])
            offer['_provider_label'] = provider['name']
            city, remote = filters.get('location_city'), filters.get('include_remote')
            if (city or remote) and not ((city and matches_city(offer, city)) or (remote and is_fully_remote(offer))):
                continue
            if filters.get('offer_source') and offer['offer_source'] != filters['offer_source']:
                continue
            if filters.get('search') and filters['search'].casefold() not in offer['offer_title'].casefold():
                continue
            if filters.get('offer_category') and offer.get('offer_category') != filters['offer_category']:
                continue
            if offer.get('offer_status', 'active') != filters.get('offer_status', 'active'):
                continue
            collected.append(offer)
        if not items or not options.get('page_param'):
            break
    return collected
