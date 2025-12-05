diagrams:
	python -m diags.generate_all
.PHONY: diagrams

build: 
	git pull && docker build -f Dockerfile -t sentinel-server:latest .
.PHONY: build

deploy:
	docker stack deploy --compose-file sentinel.yaml sentinel
.PHONY: deploy

redeploy:
	make build && docker stack rm sentinel && docker stack deploy --compose-file sentinel.yaml sentinel
.PHONY: redeploy