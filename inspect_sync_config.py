"""Show non-secret sync configuration fields only."""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

parser = argparse.ArgumentParser()
parser.add_argument('path')
args = parser.parse_args()
root = ET.parse(Path(args.path)).getroot()
gui = root.find('gui')
print(json.dumps({'path':args.path,'version':root.get('version'),
    'gui':{'enabled':gui.get('enabled') if gui is not None else None,'address':gui.findtext('address') if gui is not None else None},
    'folders':[{'id':folder.get('id'),'label':folder.get('label'),'path':folder.get('path'),'type':folder.get('type'),
        'paused':folder.findtext('paused'),'devices':[device.get('id') for device in folder.findall('device')]} for folder in root.findall('folder')],
    'devices':[{'id':device.get('id'),'name':device.get('name'),'addresses':[a.text for a in device.findall('address')]} for device in root.findall('device')],
    'listen':[a.text for a in root.findall('./options/listenAddress')]},ensure_ascii=False,indent=2))
