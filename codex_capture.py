"""Bounded, ephemeral task extraction through Codex's managed ChatGPT login."""
import json
from capture_prompt import task_prompt
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

class CaptureError(ValueError):
    pass

MODEL = "gpt-6-luna"
REASONING = "low"
verified = False

def executable():
    binary = shutil.which('codex')
    fallback = Path.home()/'.local/bin/codex'
    return binary or (str(fallback) if fallback.is_file() else None)

def environment():
    # Keep managed CODEX_HOME authentication; do not inherit API-key overrides.
    return {k:v for k,v in os.environ.items() if k not in ('OPENAI_API_KEY','OPENAI_BASE_URL','CODEX_API_KEY')}

def status():
    binary = executable()
    if not binary:
        return 'Codex CLI not installed. Install Codex, then run codex login in Terminal.'
    try:
        result = subprocess.run([binary,'login','status'],capture_output=True,text=True,timeout=5,env=environment())
    except (OSError,subprocess.TimeoutExpired):
        return 'Could not check Codex sign-in. Offline capture remains available.'
    if result.returncode or 'ChatGPT' not in result.stdout+result.stderr:
        return 'ChatGPT sign-in needed: run codex login in Terminal, then check again.'
    return 'ChatGPT signed in. Capture: GPT-6 Luna · low reasoning. ' + ('Capture inference verified this service session.' if verified else 'Inference unverified; use Test connection or draft a task.')

def extract(text, today, kinds):
    global verified
    binary = executable()
    if not binary: raise CaptureError('Codex is not installed; install it and run codex login.')
    if 'ChatGPT signed in.' not in status(): raise CaptureError('ChatGPT sign-in not confirmed. Run codex login in Terminal.')
    # Require the isolation options rather than silently downgrading on old CLIs.
    help_result = subprocess.run([binary,'exec','--help'],capture_output=True,text=True,timeout=5,env=environment())
    if help_result.returncode or any(flag not in help_result.stdout for flag in ('--ignore-user-config','--ephemeral','--output-schema')):
        raise CaptureError('Update Codex CLI: this version lacks required isolated capture options.')
    schema={'type':'object','additionalProperties':False,'properties':{k:{'type':'string'} for k in ('title','kind','group','due','due_time','next_action','url','notes')}}
    schema['properties']['kind']['enum']=kinds
    schema['properties']['minutes']={'type':'integer','minimum':0}
    schema['properties']['next_actions']={'type':'array','items':{'type':'string'},'maxItems':200}
    schema['required']=list(schema['properties'])
    prompt=task_prompt(text,today)
    with tempfile.TemporaryDirectory(prefix='aura-capture-') as folder:
        root=Path(folder); shape=root/'schema.json'; output=root/'draft.json'
        shape.write_text(json.dumps(schema))
        args=[binary,'exec','--model',MODEL,'--ignore-user-config','--ignore-rules','--ephemeral','--skip-git-repo-check',
              '--sandbox','read-only','--cd',folder,'--color','never','--json','--output-schema',str(shape),
              '--output-last-message',str(output),'-c','approval_policy="never"','-c','web_search="disabled"',
              '-c','model_provider="openai"','-c','model_reasoning_effort='+json.dumps(REASONING),'-c','tools.view_image=false',
              '-c','shell_environment_policy.inherit="none"']
        for feature in ('shell_tool','unified_exec','code_mode','code_mode_host','code_mode_only','apps','plugins','hooks','multi_agent','browser_use','browser_use_external','computer_use','image_generation'):
            args.extend(['--disable',feature])
        try:
            result=subprocess.run(args+['-'],input=prompt,capture_output=True,text=True,timeout=75,env=environment())
        except subprocess.TimeoutExpired:
            raise CaptureError('Codex timed out. Check your connection and try again.') from None
        except OSError:
            raise CaptureError('Could not start Codex CLI.') from None
        if result.returncode:
            # Never return raw CLI diagnostics: they may contain private account/config details.
            diagnostics=(result.stdout+result.stderr).lower()
            if 'rate limit' in diagnostics or 'usage limit' in diagnostics:
                raise CaptureError('ChatGPT/Codex usage limit reached. Try again later.')
            if 'unauthorized' in diagnostics or '401' in diagnostics:
                raise CaptureError('Codex sign-in expired. Run codex login in Terminal.')
            raise CaptureError('Codex could not produce a draft. Check connection, account access and CLI version.')
        try:
            data=json.loads(output.read_text())
            if set(data)!=set(schema['required']) or data['kind'] not in kinds or not data['title'].strip(): raise ValueError()
            if any(not isinstance(data[k],str) for k in schema['properties'] if k not in ('minutes','next_actions')): raise ValueError()
            if not isinstance(data['next_actions'],list) or len(data['next_actions'])>200 or any(not isinstance(t,str) or not t.strip() or len(t)>1000 for t in data['next_actions']): raise ValueError()
            if type(data['minutes']) is not int or data['minutes']<0: raise ValueError()
        except (OSError,ValueError,TypeError,KeyError):
            raise CaptureError('Codex returned an invalid draft. Try again.') from None
        verified=True
        return data
