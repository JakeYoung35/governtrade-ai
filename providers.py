"""Single catalog shared with the browser. Validation calls read-only provider APIs.

The catalog also offers the GovernTrade free agent when the server has a Gemini key for it
(GOVERNTRADE_FREE_AGENT_GEMINI_KEY): agents on it need no key of their own, and chat with them is limited to
GOVERNTRADE_FREE_AGENT_DAILY_MESSAGES per workspace per UTC day, because every workspace shares the key's free quota.
"""
import json
import os
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import quote
from urllib.error import HTTPError,URLError

FREE_PROVIDER='governtrade-free'
FREE_DEFAULT_MODEL='gemini-3.1-flash-lite'
FREE_DEFAULT_DAILY_MESSAGES=20

def free_agent_key():return os.environ.get('GOVERNTRADE_FREE_AGENT_GEMINI_KEY','').strip()
def free_agent_model():return os.environ.get('GOVERNTRADE_FREE_AGENT_MODEL','').strip() or FREE_DEFAULT_MODEL
def free_agent_daily_messages():
    try:return max(0,int(os.environ.get('GOVERNTRADE_FREE_AGENT_DAILY_MESSAGES',FREE_DEFAULT_DAILY_MESSAGES)))
    except ValueError:return FREE_DEFAULT_DAILY_MESSAGES

def catalog():
    providers=json.loads((Path(__file__).parent/'static/providers.json').read_text())
    if free_agent_key():
        limit=free_agent_daily_messages()
        providers={FREE_PROVIDER:{'label':'GovernTrade free agent (Gemini, no API key needed)','models':[free_agent_model()],
            'free':True,'dailyMessages':limit,
            'notice':f'Uses GovernTrade\'s shared Gemini free tier: up to {limit} chat messages per day for your workspace, '
                     'resetting at midnight UTC. Google may use free-tier prompts and replies to improve its products, so '
                     'do not include personal information. Choose another provider with your own key for more messages.'},
            **providers}
    return providers

def _check_remote(provider,model,key):
    if provider=='openai':url='https://api.openai.com/v1/models/'+quote(model,safe='');headers={'Authorization':'Bearer '+key}
    elif provider=='anthropic':url='https://api.anthropic.com/v1/models/'+quote(model,safe='');headers={'x-api-key':key,'anthropic-version':'2023-06-01'}
    else:url='https://generativelanguage.googleapis.com/v1beta/models/'+quote(model,safe='');headers={'x-goog-api-key':key}
    with urlopen(Request(url,headers=headers),timeout=15) as response:data=json.load(response)
    if (data.get('id') or data.get('name','').removeprefix('models/'))!=model:raise ValueError('Provider did not validate the selected model.')

def validate_model_key(provider,model,key):
    available=catalog()
    if provider==FREE_PROVIDER:raise ValueError('The free agent uses the server key; save it without an API key.')
    if provider not in available or model not in available[provider]['models']:raise ValueError('Choose a model from the selected provider catalog.')
    if not isinstance(key,str) or not key.strip():raise ValueError('Enter the provider API key.')
    try:_check_remote(provider,model,key)
    except (HTTPError,URLError,TimeoutError,ValueError):raise ValueError('Provider key validation failed. Check the key, model access and network. Agent was not saved.') from None

def validate_free_agent(model):
    """The free agent's model, checked with the server's Gemini key. The key is never shown or stored per agent."""
    key=free_agent_key()
    if not key:raise ValueError('The free agent is not available on this server.')
    if model!=free_agent_model():raise ValueError('Choose the free agent\'s model.')
    try:_check_remote('google',model,key)
    except (HTTPError,URLError,TimeoutError,ValueError):raise ValueError('The free agent could not be reached right now. Try again shortly, or use your own API key.') from None
