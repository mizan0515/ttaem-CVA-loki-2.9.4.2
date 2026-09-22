"""Naver HLS relative URI query inheritance; no account/cookie access."""
import re
from urllib.parse import urljoin, urlparse, urlunparse

def child_url(parent, child):
    parsed_parent = urlparse(parent)
    resolved = urlparse(urljoin(parent, child))
    if resolved.scheme != 'https' or resolved.hostname != parsed_parent.hostname or resolved.username or resolved.password:
        raise ValueError('Playback resource escapes the selected CDN')
    if not child.startswith(('http://', 'https://')) and not resolved.query:
        resolved = resolved._replace(query=parsed_parent.query)
    return urlunparse(resolved)

def rewrite_playlist(parent, text):
    lines=text.splitlines()
    if not lines or lines[0].strip() != '#EXTM3U' or len(lines)>100000:
        raise ValueError('Invalid HLS playlist')
    result=[]
    for line in lines:
        line=line.strip()
        if line and not line.startswith('#'):
            line=child_url(parent,line)
        elif 'URI="' in line:
            line=re.sub(r'URI="([^"]+)"',lambda match:'URI="'+child_url(parent,match[1])+'"',line)
        result.append(line)
    return '\n'.join(result)+'\n'
