"""Show only non-secret status fields from the local Syncthing API."""
import json
from pathlib import Path
import urllib.request
import xml.etree.ElementTree as ET


def main():
    config = ET.parse(Path.home()/'AppData/Local/Syncthing/config.xml').getroot()
    key = config.findtext('./gui/apikey')
    address = config.findtext('./gui/address')
    if address != '127.0.0.1:8384':
        raise RuntimeError('Unexpected local GUI address')
    def get(path):
        req = urllib.request.Request('http://'+address+'/rest/'+path, headers={'X-API-Key':key})
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.load(response)
    status = get('db/status?folder=obsidian-cours')
    allowed = ('state','error','errors','globalFiles','globalDirectories','globalBytes',
               'localFiles','localDirectories','localBytes','needFiles','needBytes',
               'receiveOnlyChangedFiles','receiveOnlyChangedBytes','pullErrors','invalid')
    connections = get('system/connections').get('connections',{})
    peers = {identity:{key:value.get(key) for key in ('connected','type','address')}
             for identity,value in connections.items()}
    errors = get('folder/errors?folder=obsidian-cours')
    print(json.dumps({'folder':{key:status.get(key) for key in allowed},
                     'connections':peers,'folder_errors':errors},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
