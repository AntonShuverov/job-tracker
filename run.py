import sys
from dotenv import load_dotenv
load_dotenv()
if "--live" in sys.argv:
    import os
    os.environ["MODE"] = "live"
from tg_parser import main
import asyncio
asyncio.run(main())
