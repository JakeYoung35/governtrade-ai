"""Single catalog shared with the browser. Validation calls read-only provider APIs."""
import json
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import quote
from urllib.error import HTTPError,URLError

def catalog():return json.loads((Path(__file__).parent/'static/providers.json').read_text())
def validate_model_key(provider,model,key):
    available=catalog()
    if provider not in available or model not in available[provider]['models']:raise ValueError('Choose a model from the selected provider catalog.')
    if not isinstance(key,str) or not key.strip():raise ValueError('Enter the provider API key.')
    if provider=='openai':url='https://api.openai.com/v1/models/'+quote(model,safe='');headers={'Authorization':'Bearer '+key}
    elif provider=='anthropic':url='https://api.anthropic.com/v1/models/'+quote(model,safe='');headers={'x-api-key':key,'anthropic-version':'2023-06-01'}
    else:url='https://generativelanguage.googleapis.com/v1beta/models/'+quote(model,safe='');headers={'x-goog-api-key':key}
    try:
        with urlopen(Request(url,headers=headers),timeout=15) as response:data=json.load(response)
        if (data.get('id') or data.get('name','').removeprefix('models/'))!=model:raise ValueError('Provider did not validate the selected model.')
    except (HTTPError,URLError,TimeoutError,ValueError):raise ValueError('Provider key validation failed. Check the key, model access and network. Agent was not saved.') from None
