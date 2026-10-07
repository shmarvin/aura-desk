import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import codex_capture as capture

class CaptureTests(unittest.TestCase):
    def setUp(self): capture.verified=False
    def run_cli(self,args,**kwargs):
        if args[1:]==['login','status']:
            return subprocess.CompletedProcess(args,0,'Logged in using ChatGPT','')
        if args[2:]==['--help']:
            return subprocess.CompletedProcess(args,0,'--ignore-user-config --ephemeral --output-schema','')
        self.command=args;self.prompt=kwargs['input'];self.env=kwargs['env']
        shape=json.loads(Path(args[args.index('--output-schema')+1]).read_text())
        data={k:'' for k in shape['required']};data.update(title='Read chapter 2',kind='Reading',minutes=0,next_actions=[])
        Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(data))
        return subprocess.CompletedProcess(args,0,'','')
    def test_isolated_capture_and_verified_status(self):
        with patch.object(capture,'executable',return_value='/fake/codex'),patch.object(capture.subprocess,'run',side_effect=self.run_cli),patch.dict(capture.os.environ,{'OPENAI_API_KEY':'secret'}):
            data=capture.extract('Read chapter 2','2026-10-07',['Reading'])
            self.assertIn('verified this service session',capture.status())
        self.assertEqual(data['due_time'],'');self.assertEqual(data['minutes'],0)
        for flag in ('--ignore-user-config','--ephemeral','--ignore-rules'): self.assertIn(flag,self.command)
        self.assertEqual(self.command[self.command.index('--sandbox')+1],'read-only')
        self.assertEqual(self.command[self.command.index('--model')+1],'gpt-6-luna')
        self.assertIn('model_reasoning_effort="low"',self.command)
        self.assertIn('shell_tool',self.command);self.assertIn('unified_exec',self.command)
        self.assertNotIn('OPENAI_API_KEY',self.env)
        self.assertFalse(Path(self.command[self.command.index('--cd')+1]).exists())
    def test_failure_does_not_verify_or_expose_diagnostics(self):
        def run(args,**kwargs):
            if args[1:]==['login','status'] or args[2:]==['--help']:return self.run_cli(args,**kwargs)
            return subprocess.CompletedProcess(args,1,'','private account information')
        with patch.object(capture,'executable',return_value='/fake/codex'),patch.object(capture.subprocess,'run',side_effect=run):
            with self.assertRaises(capture.CaptureError) as caught:capture.extract('Read','2026-10-07',['Reading'])
        self.assertNotIn('private',str(caught.exception));self.assertFalse(capture.verified)
    def test_old_cli_fails_closed(self):
        results=[subprocess.CompletedProcess([],0,'Logged in using ChatGPT',''),subprocess.CompletedProcess([],0,'old help','')]
        with patch.object(capture,'executable',return_value='/fake/codex'),patch.object(capture.subprocess,'run',side_effect=results):
            with self.assertRaisesRegex(capture.CaptureError,'Update Codex'):capture.extract('Read','2026-10-07',['Reading'])

if __name__=='__main__':unittest.main()
