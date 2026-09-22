"""Build the standalone file from the frozen controller snapshot and app body.
Run extract_controllers.py explicitly only when intentionally refreshing drivers.
"""
from pathlib import Path
support = Path(__file__).resolve().parent
output = support.parent / 'Microwave_Transport_Test_NH_dev.py'
text = (support / 'controller_snapshot.py.txt').read_text() + (support / 'app_body.py.txt').read_text()
compile(text, str(output), 'exec')
output.write_text(text)
print(output)
