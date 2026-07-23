"""twctl — the agent-facing surface for the tw campaign map.

Every command ultimately runs `tw`-package Python *inside* Unreal, either
headlessly (`-run=pythonscript`, reproducible/CI) or by pushing a snippet into a
live editor over Python Remote Execution (the tight loop). See `cli.py`.
"""
