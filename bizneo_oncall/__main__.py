from bizneo_oncall.compat import sanitize_python_sysconfig

sanitize_python_sysconfig()

from bizneo_oncall.cli import main

raise SystemExit(main())
