import math
import re
import json
import requests
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, request

import os
import random
import cloudscraper
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

_cached_online_proxies = []

def get_free_online_proxies():
    global _cached_online_proxies
    if _cached_online_proxies:
        return _cached_online_proxies
    try:
        url = 'https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=3000&country=all&ssl=all&anonymity=all'
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            proxies = [line.strip() for line in r.text.splitlines() if line.strip() and ':' in line]
            if proxies:
                random.shuffle(proxies)
                _cached_online_proxies = proxies
                return proxies
    except Exception as e:
        print("Error fetching free online proxies:", e)
    return []

def _try_proxy_fetch(proxy_str, url, headers):
    px = {'http': f'http://{proxy_str}', 'https': f'http://{proxy_str}'}
    scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
    r = scraper.get(url, headers=headers, proxies=px, timeout=5)
    if r.status_code == 200:
        t = r.text.replace('\\"', '"')
        if 'number' in t or 'initialData' in t or 'phoneNumber' in t:
            return t
    raise Exception("Proxy failed")

def make_esimplus_request(rsc_url, clean_url):
    scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
    
    # 0. Custom Environment Proxy Check
    env_proxy = os.environ.get('PROXY_URL') or os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY')
    if env_proxy:
        px = {'http': env_proxy, 'https': env_proxy}
        try:
            r = scraper.get(clean_url, headers=HEADERS, proxies=px, timeout=10)
            if r.status_code == 200:
                return r.text.replace('\\"', '"')
        except Exception as e:
            print("Env proxy failed:", e)

    # 1. Direct Cloudscraper request
    try:
        r = scraper.get(clean_url, headers=HEADERS, timeout=8)
        if r.status_code == 200:
            return r.text.replace('\\"', '"')
    except Exception as e:
        print(f"Direct request failed: {e}. Trying proxies...")

    # 2. Parallel Free Proxy Fallback
    online_proxies = get_free_online_proxies()
    if online_proxies:
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(_try_proxy_fetch, p, clean_url, HEADERS)
                for p in online_proxies[:30]
            ]
            for future in as_completed(futures):
                try:
                    res = future.result()
                    if res:
                        return res
                except Exception:
                    continue

    # Fallback to direct requests if proxy pool fails
    r = requests.get(clean_url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.text.replace('\\"', '"')

_number_country_cache = {}

def format_relative_time(date_input):
    if not date_input:
        return 'Active'
    
    if isinstance(date_input, str) and ('ago' in date_input.lower() or date_input.lower() in ['new', 'active', 'online']):
        return date_input

    try:
        if isinstance(date_input, str):
            s = date_input.replace('Z', '+00:00')
            dt = datetime.fromisoformat(s)
        else:
            dt = date_input

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
            
        now = datetime.now(timezone.utc)
        diff = now - dt

        seconds = int(diff.total_seconds())
        if seconds < 0:
            seconds = 0

        if seconds < 60:
            return f"{seconds} seconds ago" if seconds != 1 else "1 second ago"
        
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} minutes ago" if minutes != 1 else "1 minute ago"
            
        hours = minutes // 60
        if hours < 24:
            return f"{hours} hours ago" if hours != 1 else "1 hour ago"
            
        days = hours // 24
        if days < 30:
            return f"{days} days ago" if days != 1 else "1 day ago"
            
        months = days // 30
        if months < 12:
            return f"{months} months ago" if months != 1 else "1 month ago"
            
        years = months // 12
        return f"{years} years ago" if years != 1 else "1 year ago"
    except Exception:
        return str(date_input)

def get_timestamp_key(item):
    raw_iso = item.get('raw_created_at') or item.get('added_time')
    if raw_iso and isinstance(raw_iso, str) and ('T' in raw_iso or '-' in raw_iso) and 'ago' not in raw_iso.lower():
        try:
            s = raw_iso.replace('Z', '+00:00')
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            pass

    time_str = str(item.get('added', '') or item.get('added_time', '')).lower().strip()
    now = datetime.now(timezone.utc)

    if 'new' in time_str:
        return (now + timedelta(hours=1)).timestamp()

    m = re.search(r'(\d+)', time_str)
    val = int(m.group(1)) if m else 0

    if 'sec' in time_str:
        return (now - timedelta(seconds=val)).timestamp()
    if 'min' in time_str:
        return (now - timedelta(minutes=val)).timestamp()
    if 'hour' in time_str:
        return (now - timedelta(hours=val)).timestamp()
    if 'day' in time_str:
        return (now - timedelta(days=val)).timestamp()
    if 'month' in time_str:
        return (now - timedelta(days=val * 30)).timestamp()
    if 'year' in time_str:
        return (now - timedelta(days=val * 365)).timestamp()

    return 0.0

def extract_otp(text):
    if not text:
        return None
    match = re.search(r'\b\d{3}-\d{3}\b', text)
    if match:
        return match.group(0).replace('-', '')
    match = re.search(r'\b\d{4,8}\b', text)
    if match:
        return match.group(0)
    return None

def fetch_esimplus_numbers_single_page(country_slug=None, page=1):
    global _number_country_cache
    
    if country_slug:
        slug_clean = country_slug.lower().strip()
        if page > 1:
            rsc_url = f'https://esimplus.me/temporary-numbers/{slug_clean}/{page}?_rsc=5xLMCj-LWS6zTP3J'
            clean_url = f'https://esimplus.me/temporary-numbers/{slug_clean}/{page}'
        else:
            rsc_url = f'https://esimplus.me/temporary-numbers/{slug_clean}?_rsc=5xLMCj-LWS6zTP3J'
            clean_url = f'https://esimplus.me/temporary-numbers/{slug_clean}'
    else:
        if page > 1:
            rsc_url = f'https://esimplus.me/temporary-numbers/{page}?_rsc=5xLMCj-LWS6zTP3J'
            clean_url = f'https://esimplus.me/temporary-numbers/{page}'
        else:
            rsc_url = f'https://esimplus.me/temporary-numbers?_rsc=5xLMCj-LWS6zTP3J'
            clean_url = f'https://esimplus.me/temporary-numbers'

    text = make_esimplus_request(rsc_url, clean_url)

    pattern = r'\{"number":(\{.*?\}),"title":'
    matches = re.findall(pattern, text)

    numbers = []
    for m in matches:
        try:
            item = json.loads(m)
            phone_num = item.get('phoneNumber', '')
            slug = item.get('slug', '')
            c_code = item.get('countryCode', '')
            raw_created = item.get('createdAt', '')
            if phone_num and slug:
                _number_country_cache[phone_num] = slug
                
            css_flag = c_code.lower()
            if css_flag == 'uk':
                css_flag = 'gb'
            name = slug.replace('-', ' ').title()

            numbers.append({
                'name': name,
                'slug': slug,
                'link': f"/esimplus/sms/{slug}/{phone_num}",
                'code': css_flag,
                'country_code': c_code,
                'number': phone_num,
                'friendly_number': item.get('friendlyPhoneNumber', ''),
                'added_time': format_relative_time(raw_created),
                'raw_created_at': raw_created,
                'country_name': name,
                'country_slug': slug,
                'is_esim': True,
                'source': 'esimplus'
            })
        except Exception:
            continue

    pagination_match = re.search(r'\"currentPage\":(\d+),\"totalPages\":(\d+)', text)
    cur_page = int(pagination_match.group(1)) if pagination_match else page
    tot_pages = int(pagination_match.group(2)) if pagination_match else 1

    return {
        'numbers': numbers,
        'current_page': cur_page,
        'total_pages': tot_pages,
        'count': len(numbers)
    }

def fetch_all_esimplus_numbers(country_slug=None):
    first_data = fetch_esimplus_numbers_single_page(country_slug=country_slug, page=1)
    all_numbers = list(first_data['numbers'])
    tot_pages = first_data['total_pages']
    
    for p in range(2, tot_pages + 1):
        p_data = fetch_esimplus_numbers_single_page(country_slug=country_slug, page=p)
        all_numbers.extend(p_data['numbers'])
        
    all_numbers.sort(key=get_timestamp_key, reverse=True)

    countries_dict = {}
    for item in all_numbers:
        slug = item['slug']
        if slug not in countries_dict:
            countries_dict[slug] = {
                'name': item['name'],
                'slug': item['slug'],
                'link': f"/esimplus/country/{item['slug']}",
                'code': item['code'],
                'country_code': item['country_code']
            }
    countries = list(countries_dict.values())

    return {
        'numbers': all_numbers,
        'countries': countries,
        'total_pages': tot_pages,
        'total_numbers': len(all_numbers)
    }

def fetch_esimplus_sms_paginated(country_slug, number_str, page=1, per_page=10):
    raw_num = str(number_str).replace('+', '').strip()
    slug = country_slug.lower().strip()
    
    rsc_url = f'https://esimplus.me/temporary-numbers/{slug}/{raw_num}?_rsc=5xLMCj-LWS6zTP3J'
    clean_url = f'https://esimplus.me/temporary-numbers/{slug}/{raw_num}'
    
    text = make_esimplus_request(rsc_url, clean_url)

    messages = []
    match = re.search(r'\"initialData\":\s*\{\s*\"data\"\s*:\s*(\[.*?\])\s*,\s*\"', text, re.DOTALL)
    if not match:
        match = re.search(r'(\[\{\"provider\":\".*?\"\}\])', text, re.DOTALL)

    if match:
        raw_json_str = match.group(1)
        try:
            raw_msgs = json.loads(raw_json_str)
            for item in raw_msgs:
                body = item.get('body', '')
                raw_time = item.get('receivedAt', '')
                messages.append({
                    'sender': item.get('friendlyFrom') or item.get('from', 'Unknown'),
                    'receiver': item.get('friendlyTo') or item.get('to', raw_num),
                    'body': body,
                    'otp': extract_otp(body),
                    'time': format_relative_time(raw_time),
                    'raw_time': raw_time
                })
        except Exception as e:
            print(f"Error parsing SMS JSON: {e}")

    messages.sort(key=lambda x: x.get('raw_time', ''), reverse=True)

    total_messages = len(messages)
    max_page = max(1, math.ceil(total_messages / per_page)) if total_messages > 0 else 1
    page = max(1, min(page, max_page))

    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    paginated_msgs = messages[start_idx:end_idx]

    return {
        'number': raw_num,
        'country_slug': slug,
        'current_page': page,
        'max_page': max_page,
        'total_pages': max_page,
        'count': len(paginated_msgs),
        'total_messages': total_messages,
        'messages': paginated_msgs
    }

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        'service': 'eSIM Plus Standalone API',
        'status': 'Online',
        'endpoints': {
            'number_list_all': '/esimplus/numbers',
            'country_numbers': '/esimplus/country/<country_slug>',
            'sms_messages': '/esimplus/sms/<country_slug>/<number>?page=1'
        }
    })

@app.route('/esimplus/numbers', methods=['GET'])
@app.route('/esimplus/numbers/<int:page>', methods=['GET'])
def get_numbers_list(page=None):
    try:
        country_slug = request.args.get('country') or request.args.get('country_slug')
        single_page_requested = request.args.get('single_page', 'false').lower() == 'true'
        
        if page is not None or single_page_requested:
            p = page or request.args.get('page', 1, type=int)
            data = fetch_esimplus_numbers_single_page(country_slug=country_slug, page=p)
            return jsonify({
                'success': True,
                'service': 'eSIM Plus',
                'current_page': data['current_page'],
                'total_pages': data['total_pages'],
                'count': data['count'],
                'numbers': data['numbers']
            })

        data = fetch_all_esimplus_numbers(country_slug=country_slug)
        return jsonify({
            'success': True,
            'service': 'eSIM Plus',
            'current_page': 1,
            'total_pages': data['total_pages'],
            'total_numbers': data['total_numbers'],
            'countries': data['countries'],
            'numbers': data['numbers']
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/esimplus/country/<slug>', methods=['GET'])
@app.route('/esimplus/country/<slug>/<int:page>', methods=['GET'])
def get_country_numbers(slug, page=None):
    try:
        data = fetch_all_esimplus_numbers(country_slug=slug)
        return jsonify({
            'success': True,
            'service': 'eSIM Plus',
            'country_slug': slug,
            'country_name': slug.replace('-', ' ').title(),
            'current_page': 1,
            'total_pages': data['total_pages'],
            'total_numbers': data['total_numbers'],
            'numbers': data['numbers']
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/esimplus/sms', methods=['GET'])
@app.route('/esimplus/sms/<number>', methods=['GET'])
@app.route('/esimplus/sms/<country>/<number>', methods=['GET'])
@app.route('/esimplus/sms/<country>/<number>/<int:page>', methods=['GET'])
def get_sms_messages(country=None, number=None, page=None):
    try:
        if not number:
            number = request.args.get('number') or request.args.get('num')
            
        if not number:
            return jsonify({'success': False, 'error': 'Parameter number is required'}), 400

        clean_number = str(number).replace('+', '').strip()
        
        if not country:
            country = request.args.get('country') or request.args.get('country_slug')

        if not country:
            country = _number_country_cache.get(clean_number)
            if not country:
                fetch_all_esimplus_numbers()
                country = _number_country_cache.get(clean_number)

        if not country:
            return jsonify({
                'success': False,
                'error': f'Country not specified and could not auto-detect for number {clean_number}. Please pass ?country=slug parameter.'
            }), 400

        if page is None:
            page = request.args.get('page', 1, type=int)

        data = fetch_esimplus_sms_paginated(country, clean_number, page=page, per_page=10)
        return jsonify({
            'success': True,
            'service': 'eSIM Plus',
            'number': data['number'],
            'country_slug': data['country_slug'],
            'current_page': data['current_page'],
            'max_page': data['max_page'],
            'total_pages': data['total_pages'],
            'count': data['count'],
            'total_messages': data['total_messages'],
            'messages': data['messages']
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    print("=====================================================")
    print(" Starting eSIM Plus Standalone API Server at http://127.0.0.1:5000")
    print(" Endpoints:")
    print("  1. All Numbers List: http://127.0.0.1:5000/esimplus/numbers")
    print("  2. Country Numbers:  http://127.0.0.1:5000/esimplus/country/canada")
    print("  3. SMS Messages:     http://127.0.0.1:5000/esimplus/sms/canada/18254137435")
    print("=====================================================")
    app.run(host='0.0.0.0', port=5000, debug=True)
