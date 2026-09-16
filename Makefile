.PHONY: generate test serve opc verify frontend

generate:
	PYTHONPATH=src python -m graphene_demo_twin.schema.generate

test:
	PYTHONPATH=src pytest -q

verify: generate test

serve: generate
	PYTHONPATH=src uvicorn graphene_demo_twin.admin.app:app --host 127.0.0.1 --port 8080

opc: generate
	PYTHONPATH=src python -m graphene_demo_twin.cli.main opc

frontend:
	cd apps/web && npm install && npm run build
