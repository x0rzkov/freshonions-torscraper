import scrapy
import urlparse 
from collections import *
from pony.orm import *
from datetime import *
from tor_db import *


from scrapy.exceptions import IgnoreRequest


@db_session
def domain_urls_down():
    urls = []
    now = datetime.now()
    event_horizon = now - timedelta(days=30)
    n_items = count(d for d in Domain if d.last_alive > event_horizon and d.is_up == False)
    for domain in Domain.select(lambda d: d.is_up == False and d.last_alive > event_horizon).random(n_items):
        urls.append(domain.index_url())
    return urls

@db_session
def domain_urls_resurrect():
    urls = []
    now = datetime.now()
    event_horizon = now - timedelta(days=30)
    n_items = count(d for d in Domain if d.last_alive > event_horizon and d.is_up == False)
    for domain in Domain.select(lambda d: d.is_up == False and d.last_alive < event_horizon).random(n_items):
        urls.append(domain.index_url())
    return urls

@db_session
def domain_urls():
    urls = []
    for domain in Domain.select():
        urls.append(domain.index_url())
    return urls

@db_session
def domain_urls_recent_no_crap():
    urls = []
    now = datetime.now()
    event_horizon = now - timedelta(days=30)
    n_items = count(d for d in Domain if d.is_up == True and d.is_crap == False)
    for domain in Domain.select(lambda d: d.is_up == True and d.is_crap == False).random(n_items):
        urls.append(domain.index_url())
    return urls

@db_session
def domain_urls_recent():
    urls = []
    now = datetime.now()
    event_horizon = now - timedelta(days=30)
    n_items = count(d for d in Domain if d.last_alive > event_horizon)
    for domain in Domain.select(lambda d: d.last_alive > event_horizon).random(n_items):
        urls.append(domain.index_url())
    return urls

class TorSpider(scrapy.Spider):
    name = "tor"
    allowed_domains = ['onion']
    handle_httpstatus_list = [404, 403, 401, 503, 500, 504]
    start_urls = domain_urls_recent_no_crap()
    if len(start_urls) == 0:
        start_urls = [
            'http://gxamjbnu7uknahng.onion/',
            'http://mijpsrtgf54l7um6.onion/',
            'http://dirnxxdraygbifgc.onion/',
            'http://torlinkbgs6aabns.onion/'
        ]

    custom_settings = {
        'DOWNLOAD_MAXSIZE': (1024 * 1024),
        'ROBOTSTXT_OBEY': False,
	    'CONCURRENT_REQUESTS' : 24,
        'DEPTH_PRIORITY' : 8,
        'DOWNLOAD_TIMEOUT': 90,
        'RETRY_TIMES': 1,
        'MAX_PAGES_PER_DOMAIN' : 1000,
        'HTTPERROR_ALLOWED_CODES': handle_httpstatus_list,
        'RETRY_HTTP_CODES': [],
        'DOMAIN_IS_DEAD_TIMEOUT_MINUTES' : 5,
        'DOWNLOADER_MIDDLEWARES' : {
            'torscraper.middlewares.FilterDomainByPageLimitMiddleware' : 551,
            'torscraper.middlewares.FilterTooManySubdomainsMiddleware' : 550,
            'torscraper.middlewares.FilterDeadDomainMiddleware' : 556
         },
        'USER_AGENT': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/55.0.2883.87 Safari/537.36'
    }
    spider_exclude = [
        'blockchainbdgpzk.onion',
    ]

    

    def __init__(self, *args, **kwargs):
        super(TorSpider, self).__init__(*args, **kwargs)
        if hasattr(self, "passed_url"):
            self.start_urls = [self.passed_url]
        elif hasattr(self, "load_links") and self.load_links == "downonly":
            self.start_urls = domain_urls_down()
        elif hasattr(self, "load_links") and self.load_links == "resurrect":
            self.start_urls = domain_urls_resurrect()
        elif hasattr(self, "test") and self.test == "yes":
            self.start_urls = domain_urls_recent()
        else:
            self.start_urls = domain_urls_recent_no_crap()


    @db_session
    def update_page_info(self, url, title, code):
        if not Domain.is_onion_url(url):
            return False

        failed_codes = [666, 503, 504]
        if not title:
            title = ''
        parsed_url = urlparse.urlparse(url)
        host  = parsed_url.hostname
        if host == "zlal32teyptf4tvi.onion":
            return False

        port  = parsed_url.port
        ssl   = parsed_url.scheme=="https://"
        path  = '/' if parsed_url.path=='' else parsed_url.path
        is_up = not code in failed_codes
        if not port:
            if ssl:
                port = 443
            else:
                port = 80
            
        now = datetime.now()
      
        domain = Domain.get(host=host, port=port, ssl=ssl)
        is_crap = False
        if not domain:
            if is_up:
                last_alive = now
            else:
                last_alive = NEVER
                title=''
            domain=Domain(host=host, port=port, ssl=ssl, is_up=is_up, last_alive=last_alive, created_at=now, visited_at=now, title=title)
            self.log("created domain %s" % host)
        else:
            domain.is_up      = is_up
            domain.visited_at = now
            if is_up:

                if domain.last_alive == NEVER:
                    domain.created_at = now

                domain.last_alive = now

                if not domain.title or domain.title=='' or path=='/':
                    if not (domain.title != '' and title == ''):
                        domain.title = title

        page = Page.get(url=url)
        if not page:
            page = Page(url=url, title=title, code=code, created_at=now, visited_at=now, domain=domain)
        else:
            if is_up:
                page.title = title
            page.code = code
            page.visited_at = now
       
        return page

    @db_session
    def parse(self, response):
        title = response.css('title::text').extract_first()
        parsed_url = urlparse.urlparse(response.url)
        host  = parsed_url.hostname
        if host != "zlal32teyptf4tvi.onion":  
            self.log('Got %s (%s)' % (response.url, title))
            page = self.update_page_info(response.url, title, response.status)
            got_server_response = page.got_server_response()
            commit()
            link_to_list = []
            if (not hasattr(self, "test") or self.test != "yes") and not host in TorSpider.spider_exclude:
                for url in response.xpath('//a/@href').extract():
                    try:
                        yield scrapy.Request(url, callback=self.parse)
                        if got_server_response and Domain.is_onion_url(url):
                            parsed_link = urlparse.urlparse(url)
                            link_host   = parsed_link.hostname
                            if host != link_host:
                                link_to_list.append(url)
                    except:
                        continue

            if page.got_server_response():
                page.links_to.clear()
                for url in link_to_list:
                    link_to = Page.find_stub_by_url(url)
                    page.links_to.add(link_to)

                commit()                        


    def process_exception(self, response, exception, spider):
        self.update_page_info(response.url, None, 666);
