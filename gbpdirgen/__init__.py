#!/usr/bin/env python3
import argparse
import csv
import os
import io
import subprocess
from dataclasses import dataclass
from enum import IntEnum
from typing import List
from urllib.parse import quote_plus

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from jinja2 import Environment, PackageLoader, select_autoescape
import minify_html


TOKEN_PATH = os.path.join(os.path.dirname(__file__), 'token.json')
SCOPES = [
    'https://www.googleapis.com/auth/drive',
]


class Columns(IntEnum):
    FIRST_NAME = 3
    LAST_NAME = 4
    PHONE = 5
    EMAIL = 7
    COMPANY_NAME = 8
    ADDRESS_1 = 10
    ADDRESS_2 = 11
    CITY = 12
    STATE = 13
    ZIP = 14
    WEBSITE = 15
    LOGO = 16
    PRODUCTS = 18
    CATEGORIES = 20


class Products(IntEnum):
    BASIC = 0
    ENHANCED = 1
    PREMIUM = 2


@dataclass
class Listing():
    """Object representing a directory listing"""
    product: int
    contact_name: str
    business_name: str
    address: List[str]
    city: str
    state: str
    zip_code: str
    logo_url: str
    website_url: str
    categories: List[str]
    email: str
    phone: str

    def __eq__(self, other):
        return other.business_name == self.business_name

    @classmethod
    def from_row(cls, row: List[str]):
        row = [col.strip() for col in row]
        kwargs = {
            'product': Products.PREMIUM
            if 'Premium' in row[Columns.PRODUCTS]
            else int('Enhanced' in row[Columns.PRODUCTS]),
            'contact_name': f'{row[Columns.FIRST_NAME]} {row[Columns.LAST_NAME]}',
            'business_name': row[Columns.COMPANY_NAME],
            'address': [
                row[Columns.ADDRESS_1],
                row[Columns.ADDRESS_2],
            ],
            'city': row[Columns.CITY],
            'state': row[Columns.STATE],
            'zip_code': row[Columns.ZIP],
            'logo_url': row[Columns.LOGO],
            'website_url': row[Columns.WEBSITE]
            if row[Columns.WEBSITE].startswith('http')
            else f'https://{row[Columns.WEBSITE]}',
            'categories': [c.strip() for c in row[Columns.CATEGORIES].split(',')],
            'phone': row[Columns.PHONE],
            'email': row[Columns.EMAIL],
        }
        return cls(**kwargs)


def parse_args():
    DRIVE_FILE_ID = os.getenv('GBPDIRGEN_FILE_ID')
    parser = argparse.ArgumentParser(
        prog='gbpdirgen',
        description='A COMPLETELY NEW AND DIFFERENT MESSAGE???')
    parser.add_argument(
        '--drive_id',
        '-d',
        help='Id of input Google Drive file. Ignored if --file provided.',
        default=DRIVE_FILE_ID,
        required=not bool(DRIVE_FILE_ID))
    parser.add_argument(
        '--file',
        '-f',
        help='Path to the csv input file.')
    parser.add_argument(
        '--out',
        '-o',
        help='Path to the destination output file. Will be overwritten if it already exists.'
        'Ignored if --copy is specified.',
        default='./directory.html')
    parser.add_argument(
        '--copy',
        '-c',
        help='If specified, the output will automatically be copied to the clipboard using xclip'
        'instead of being written to a file.',
        action='store_true')
    args = parser.parse_args()
    drive_id = args.drive_id
    filename = args.file
    output_filename = args.out
    copy_to_clipboard = args.copy
    if not output_filename:
        raise Exception('No output filename provided')
    return (drive_id, filename, output_filename, copy_to_clipboard)


def listings_from_file(filename):
    # open file, read with csv reader.
    listings = []
    with open(filename, 'r') as file:
        csv_reader = csv.reader(file)
        next(csv_reader)
        for row in csv_reader:
            listing = Listing.from_row(row)
            if listing not in listings:
                listings.append(listing)
    return listings


def listings_from_drive(drive_id, service):
    request = service.files().export(fileId=drive_id, mimeType='text/csv')
    bfh = io.BytesIO()
    downloader = MediaIoBaseDownload(bfh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    bfh.seek(0)
    fh = io.StringIO()
    fh.write(bfh.getvalue().decode('utf8'))
    fh.seek(0)
    csv_reader = csv.reader(fh)
    next(csv_reader)
    listings = []
    for row in csv_reader:
        listing = Listing.from_row(row)
        if listing not in listings:
            listings.append(listing)
    bfh.close()
    fh.close()
    return listings


def get_drive_service():
    CREDENTIALS_PATH = os.getenv('GBPDIRGEN_CREDENTIALS') or f'{os.getenv("HOME")}/.gbpdirgen/credentials.json'
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(
            TOKEN_PATH,
            SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, 'w') as token_file:
            token_file.write(creds.to_json())
    service = build('drive', 'v3', credentials=creds)
    return service


def main():
    drive_id, input_filename, output_filename, copy_to_clipboard = parse_args()

    # setup jinja
    def uriencode(value: str):
        return quote_plus(value)
    env = Environment(
        loader=PackageLoader(__name__, 'templates'),
        autoescape=select_autoescape(['html']))
    env.filters['uriencode'] = uriencode

    # setup google drive api
    service = get_drive_service()

    # generate listing objects
    if drive_id:
        listings = listings_from_drive(drive_id, service)
    elif input_filename:
        listings = listings_from_file(input_filename)

    # render output
    listings = sorted([listing for listing in listings], key=lambda l: l.business_name)
    categories = sorted({cat for listing in listings for cat in listing.categories})
    premium_listings = filter(lambda l: l.product is Products.PREMIUM, listings)
    enhanced_listings = filter(lambda l: l.product is Products.ENHANCED, listings)
    basic_listings = listings
    template = env.get_template('directory.html')
    html = minify_html.minify(template.render(
        premium_listings=premium_listings,
        enhanced_listings=enhanced_listings,
        basic_listings=basic_listings,
        categories=categories))

    if copy_to_clipboard:
        p = subprocess.Popen(['xclip', '-selection', 'c'], stdin=subprocess.PIPE, close_fds=True)
        p.communicate(input=html.encode('utf8'))
        print(f'Copied {len(listings)} listings to clipboard.')

    elif output_filename:
        with open(output_filename, 'w') as output_file:
            output_file.write(html)
        print(f'Wrote {len(listings)} listings to {output_filename}')
