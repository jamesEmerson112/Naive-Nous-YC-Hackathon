{{task}}

## How to work
Each reply: one short thought, then exactly one bash command. Every command runs in a new shell in the robot app's directory.
{%- if system == "Darwin" %}
This machine runs macOS: edit files in place with `sed -i '' 's/old/new/' file.py`, not `sed -i`.
{%- endif %}
When you are done, run `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` as a command of its own. After that you can't run anything else.
