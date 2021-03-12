lint:
	flake8 rotkehlchen/ substrate_indexer/ tools/data_faker
	mypy rotkehlchen/ substrate_indexer/ tools/data_faker
	pylint --rcfile .pylint.rc rotkehlchen/ substrate_indexer/ tools/data_faker

clean:
	rm -rf build/ rotkehlchen_py_dist/ htmlcov/ rotkehlchen.egg-info/ *.dmg frontend/app/dist/

docker-image:
	packaging/docker-image.sh
