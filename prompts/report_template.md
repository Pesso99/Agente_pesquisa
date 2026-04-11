# Monitor diario - promocoes e campanhas financeiras

**Data:** {{ report_date }}
**Resumo executivo:** {{ summary }}

## Novas campanhas confirmadas
{% for item in validated_items %}
### {{ item.institution_name }} - {{ item.campaign_name }}
- Beneficio: {{ item.benefit }}
- Canal: {{ item.channel }}
- Prazo: {{ item.deadline }}
- Score: {{ item.confidence_final }}
- Evidencias: {{ item.evidence_refs }}
{% endfor %}

## Campanhas em revisao
{% for item in review_items %}
### {{ item.institution_name }} - {{ item.campaign_name }}
- Motivo: {{ item.validation_notes }}
- Score: {{ item.confidence_final }}
{% endfor %}
