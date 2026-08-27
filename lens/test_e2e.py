import os
from dotenv import load_dotenv
load_dotenv()

from retrieval.router import route_query
from retrieval.pageindex_retriever import retrieve_from_forest
from retrieval.sql_retriever import retrieve_from_sql
from synthesis.synthesiser import synthesise

q = 'What are the main pain points of mixer grinder users?'
print(f'\n=== QUERY: {q} ===')
plan = route_query(q)
print(f'Routing: qual={plan.get("needs_qual")}, quant={plan.get("needs_quant")}')

qual = []
quant = {}
if plan.get('needs_qual') and plan.get('relevant_doc_ids'):
    qual = retrieve_from_forest(q, plan['relevant_doc_ids'])
    print(f'Qual passages: {len(qual)}')
if plan.get('needs_quant') and plan.get('sql_intent'):
    quant = retrieve_from_sql(plan['sql_intent'])
    print(f'Quant rows: {quant.get("row_count", 0)}')

result = synthesise(q, qual, quant)
print(f'Confidence: {result.get("confidence")}')
print(f'Answer preview: {result.get("answer", "")[:200]}...')

