.PHONY: git-deploy-admin test-go test-python

git-deploy-admin:
	cd cmd/git-deploy-admin && go build -o ../../git-deploy-admin .

test-go:
	cd cmd/git-deploy-admin && go test ./...

test-python:
	python3 -m unittest discover -s tests -q
