"""Entirely fictional examples; never use these as retailer data."""

def product(pid='demo-adult', kind='dry', stage='adult', nutrition=None, prescription=False):
    nutrition=nutrition or 'protein 36%; fat 16%; fiber 3%; ash 7%; moisture 8%; phosphorus 1.0%; calcium 1.2%; magnesium 0.08%'
    description=f'Complete food for {stage} cats. {kind} food. '+('Veterinary prescription diet. ' if prescription else '')
    return {'source_site':'demo','product_id':pid,'name':{'adult':'示例配方 · 成猫鸡肉','kitten':'示例配方 · 幼猫成长','senior':'示例配方 · 老年猫'}[stage]+(' · 湿粮' if kind=='wet' else ' · 干粮'), 'brand':'PawBase 虚构样例','source_url':None,'fetched_at':'2026-10-07T00:00:00+00:00','product_type':kind,'product_description_raw':description,'page_text_snapshot':'虚构测试记录\n'+description+'\n'+nutrition,'breadcrumbs':[], 'variants':[{'sku':pid+'-1','name':'单件 400 g','offers':[{'@type':'Offer','price':9.90,'priceCurrency':'EUR'}]}], 'ingredient_blocks':[{'scope':'page_description','variant':None,'raw':'示例原料：鸡肉、米、矿物质；添加 taurine。'}], 'nutrition_blocks':[{'scope':'page_description','variant':None,'raw':nutrition}], 'warnings':[]}
