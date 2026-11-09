"""Makes the hoi4-bridge client importable: HOI4_BRIDGE_CLIENT, else ../../hoi4-bridge/client next to this repo."""
import os
import sys

CLIENT = os.environ.get("HOI4_BRIDGE_CLIENT",
                        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hoi4-bridge", "client"))
sys.path.insert(0, os.path.abspath(CLIENT))
