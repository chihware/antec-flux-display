.PHONY: test check
test:
	python3 -m unittest discover -s tests -v

check: test
	python3 -m py_compile antec-flux-display.py flux_sensors.py
	bash -n install.sh
