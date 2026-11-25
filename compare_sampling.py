#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Comparison of sampling designs on the same finite population (no LLM, no raw JSON)."""
import math, statistics, json
from collections import Counter, defaultdict
from pathlib import Path

PERIODS=('<=2020','2021-2023','2024+')
POLICIES=('equal','proportional','hybrid')
FEATURES=('strict_v1','any_regex_v1','multiple_majors','long_text','words')

def constrained_round(cap, targets, total, minimum):
    keys=sorted(cap)
    n={k:min(cap[k],minimum[k] if isinstance(minimum,dict) else minimum) for k in keys}
    if total<sum(n.values()) or total>sum(cap.values()):
        raise ValueError(f'budget {total}, minimum {sum(n.values())}, capacity {sum(cap.values())}')
    while sum(n.values())<total:
        k=max((k for k in keys if n[k]<cap[k]),key=lambda k:(targets[k]-n[k],k))
        n[k]+=1
    return n

def design_quota(cap,total,policy='equal',period_min=2):
    """Fixed group quota, then proportional by period; every nonempty cell has positive probability."""
    if policy not in POLICIES: raise ValueError('unknown allocation')
    if period_min<1: raise ValueError('period minimum must be >=1')
    by=defaultdict(dict)
    for (g,p),N in cap.items():
        if N>0: by[g][p]=N
    if not by: raise ValueError('empty frame')
    G={g:sum(v.values()) for g,v in by.items()}; N=sum(G.values()); K=len(G)
    alpha={'equal':1.,'proportional':0.,'hybrid':.5}[policy]
    target={g:total*(alpha/K+(1-alpha)*v/N) for g,v in G.items()}
    floor={g:sum(min(period_min,v) for v in b.values()) for g,b in by.items()}
    q=constrained_round(G,target,total,floor)
    result={}
    for g,b in by.items():
        t={p:q[g]*v/G[g] for p,v in b.items()}
        for p,n in constrained_round(b,t,q[g],period_min).items(): result[g,p]=n
    return result

def scheme_maps(mapping, labels, v1_top):
    """Alternative mappings are hypotheses, not official crosswalks."""
    def rename(overrides): return {c:overrides.get(c,g) for c,g in mapping.items()}
    fold8={'math_it':'math_physics','physics':'math_physics','social':'society_humanities','humanities':'society_humanities'}
    m8={c:fold8.get(g,g) for c,g in mapping.items()}
    m6={c:('natural' if g in ('math_it','physics','chemistry','biology','earth') else g) for c,g in mapping.items()}
    # FORD-inspired manual crosswalk, not actual OECD metadata. Engineering tech disciplines moved.
    for c in ('50','53','61','62','65','66','70','87','89','90'): 
        if c in m6: m6[c]='engineering'
    for c in ('14','15','77'):
        if c in m6: m6[c]='social'
    if '20' in m6: m6['20']='social'  # information science; ambiguity documented
    m12=dict(mapping)
    for c in ('44','45','47','49','59','90'): 
        if c in m12: m12[c]='energy_electronics_instruments'
    for c in ('67','73','75'): 
        if c in m12: m12[c]='infrastructure_transport'
    m10_alt=dict(mapping)
    for c,g in {'14':'social','15':'social','77':'medicine','83':'math_it','53':'engineering','89':'engineering','50':'engineering'}.items():
        if c in m10_alt: m10_alt[c]=g
    return {
        'year_only':{c:'all' for c in mapping},
        'grnti69':{c:c for c in mapping},
        'v1_top_other':{c:c if c in v1_top else 'OTHER' for c in mapping},
        'broad6':m6,'merged8':m8,'current10':dict(mapping),'alternative10':m10_alt,'split12':m12,
    }

def absence(N,K,n):
    if not K or not n:return 1.
    if n>N-K:return 0.
    return math.exp(sum(math.log1p(-K/(N-i)) for i in range(n)))

class Cell:
    def __init__(self):
        self.N=0; self.code=Counter(); self.ref=Counter(); self.s=[0.]*len(FEATURES); self.ss=[0.]*len(FEATURES)
    def add(self,codes,ref,features):
        self.N+=1; self.code.update(codes); self.ref[ref]+=1
        for i,x in enumerate(features): self.s[i]+=x; self.ss[i]+=x*x
    def variance(self,i):
        return max(0.,(self.ss[i]-self.s[i]**2/self.N)/(self.N-1)) if self.N>1 else 0.

def assess(cells,quota,refs,codes):
    N=sum(c.N for c in cells.values()); n=sum(quota.values())
    if set(quota)!=set(cells) or any(not 0<quota[k]<=c.N for k,c in cells.items()): raise ValueError('invalid quota')
    # Horvitz-Thompson stratified mean. Finite population exact variance for known proxy outcomes.
    result={'N':N,'n':n,'strata':len(cells),'min_cell_n':min(quota.values()),
        'noncensus_singletons':sum(quota[k]==1 and c.N>1 for k,c in cells.items()),
        'kish_n':N*N/sum(c.N*c.N/quota[k] for k,c in cells.items()),
        'min_weight':min(c.N/quota[k] for k,c in cells.items()),'max_weight':max(c.N/quota[k] for k,c in cells.items())}
    result['kish_efficiency']=result['kish_n']/n
    raw_abs={code:1. for code in codes}; raw_N=Counter(); ref_abs={g:1. for g in refs}; ref_expected=Counter(); ref_N=Counter()
    own=Counter()
    for k,c in cells.items():
        nh=quota[k]; own[k[0]]+=nh; ref_N.update(c.ref); raw_N.update(c.code)
        for code,K in c.code.items():
            if code in raw_abs: raw_abs[code]*=absence(c.N,K,nh)
        for g,K in c.ref.items():
            ref_abs[g]*=absence(c.N,K,nh); ref_expected[g]+=nh*K/c.N
    result.update(groups=len(own),min_group_n=min(own.values()),max_group_n=max(own.values()),
        expected_raw_codes=sum(1-x for x in raw_abs.values()),raw_codes_observed=len(codes),
        min_ref10_expected=min(ref_expected[g] for g in refs),max_ref10_expected=max(ref_expected[g] for g in refs),
        max_ref10_absence=max(ref_abs.values()),ref10_equal_tv=.5*sum(abs(ref_expected[g]/n-1/len(refs)) for g in refs),
        ref10_population_tv=.5*sum(abs(ref_expected[g]/n-ref_N[g]/N) for g in refs))
    result['raw_codes_probability_ge_50pct']=sum(1-x>=.5 for x in raw_abs.values())
    result['expected_rare_codes_lt_1pct']=sum(1-x for code,x in raw_abs.items() if raw_N[code]/N<.01)
    result['rare_codes_lt_1pct']=sum(raw_N[code]/N<.01 for code in codes)
    # Maximum design variance over arbitrary binary values independently within each stratum.
    vmax=sum((c.N/N)**2*(1-quota[k]/c.N)*(c.N//2)*(c.N-c.N//2)/(c.N*(c.N-1))/quota[k] for k,c in cells.items() if c.N>1)
    result['binary_worst_case_se']=math.sqrt(vmax)
    result['binary_worst_case_normal95_halfwidth']=1.96*math.sqrt(vmax)
    proxy=[]
    for i,name in enumerate(FEATURES):
        total=sum(c.s[i] for c in cells.values()); squares=sum(c.ss[i] for c in cells.values())
        S2=max(0.,(squares-total*total/N)/(N-1)) if N>1 else 0.
        v=sum((c.N/N)**2*(1-quota[k]/c.N)*c.variance(i)/quota[k] for k,c in cells.items())
        srs=(1-n/N)*S2/n
        proxy.append(dict(feature=name,population_mean=total/N,design_se=math.sqrt(v),srs_se=math.sqrt(srs),variance_ratio_vs_srs=v/srs if srs else None,normal_95_halfwidth=1.96*math.sqrt(v)))
    result['mean_proxy_variance_ratio']=statistics.mean(x['variance_ratio_vs_srs'] for x in proxy if x['variance_ratio_vs_srs'] is not None) if any(x['variance_ratio_vs_srs'] is not None for x in proxy) else None
    return result,proxy,[dict(reference_group=g,N=ref_N[g],expected_n=ref_expected[g],probability_absent=ref_abs[g]) for g in sorted(refs)],[dict(code=c,N_with_code=raw_N[c],probability_present=1-raw_abs[c]) for c in sorted(raw_abs)]

def compare(pool,mapping,names,labels,v1_top,seed,out,budgets,period_min,write_csv,write_json,digest):
    maps=scheme_maps(mapping,labels,v1_top); refs=sorted(names)
    print(f'Comparing {len(maps)} mappings on {len(pool)} records...',flush=True)
    # Reuse exactly the production assignment for a mapping: hash among distinct groups.
    base=[]
    for r in pool:
        codes=tuple(c for c in r['major_codes'].split('|') if c in mapping)
        if not codes: raise ValueError('common frame must have at least one mapped code')
        signal=int(r['explicit_signal']); words=int(r['words'])
        features=(int(signal==2),int(signal>=1),int(len(r['major_codes'].split('|'))>1),int(words>=354),words)
        h=int(digest(f'{seed}|{r["reg_key"]}|grnti')[:16],16)
        base.append((codes,r['category'],r['period_v2'],features,h))
    common_codes=sorted({c for row in base for c in row[0]}); summaries=[]; variances=[]; references=[]; coverage=[]; plans=[]; failures=[]
    for scheme,mp in maps.items():
        allcells=defaultdict(Cell); strictcells=defaultdict(Cell)
        for codes,ref,p,features,h in base:
            groups=sorted({mp[c] for c in codes}); g=groups[h%len(groups)]
            k=(g,p); allcells[k].add(codes,ref,features)
            if features[0]: strictcells[k].add(codes,ref,features)
        for pool_id,cells in [('G',allcells),('E_v1_proxy',strictcells)]:
            cap={k:c.N for k,c in cells.items()}; active_refs=refs
            observed_codes=sorted({c for cell in cells.values() for c in cell.code})
            for budget in budgets:
                for policy in POLICIES:
                    tag=dict(scheme=scheme,pool=pool_id,budget=budget,allocation=policy,period_min=period_min)
                    try:
                        if {g for g,p in cap}!=set(mp.values()): raise ValueError('configured group absent from this pool')
                        quota=design_quota(cap,budget,policy,period_min)
                    except ValueError as e:
                        failures.append(dict(tag,reason=str(e))); continue
                    res,proxy,refrows,coderows=assess(cells,quota,active_refs,observed_codes)
                    summaries.append(dict(tag,**res)); variances.extend(dict(tag,**x) for x in proxy); references.extend(dict(tag,**x) for x in refrows); coverage.extend(dict(tag,**x) for x in coderows)
                    plans.extend(dict(tag,category=g,period=p,N=cap[g,p],n=n,probability=n/cap[g,p],weight=cap[g,p]/n) for (g,p),n in sorted(quota.items()))
        print('Compared',scheme,flush=True)
    # Pareto frontier, no invented scalar weights. Fixed metrics and fixed evaluation domains across schemes.
    for row in summaries:
        peers=[p for p in summaries if p['pool']==row['pool'] and p['budget']==row['budget']]
        def obj(x):return (-x['kish_efficiency'],-x['expected_raw_codes'],x['ref10_equal_tv'],x['mean_proxy_variance_ratio'] or 0.)
        a=obj(row); row['pareto']=not any(all(b<=c+1e-12 for b,c in zip(obj(p),a)) and any(b<c-1e-12 for b,c in zip(obj(p),a)) for p in peers)
    write_csv(out/'comparison_summary.csv',summaries); write_csv(out/'comparison_proxy_variance.csv',variances)
    write_csv(out/'comparison_reference10.csv',references); write_csv(out/'comparison_raw_code_coverage.csv',coverage)
    write_csv(out/'comparison_quotas.csv',plans); write_csv(out/'comparison_infeasible.csv',failures,['scheme','pool','budget','allocation','period_min','reason'])
    write_csv(out/'comparison_mappings.csv',[dict(scheme=s,code=c,label=labels[c],group=g) for s,mp in maps.items() for c,g in sorted(mp.items())])
    # Candidate mappings can be used directly by the main pilot script. Not auto-selected.
    for s,mp in maps.items():
        gs=defaultdict(dict)
        for c,g in mp.items():gs[g][c]=labels[c]
        write_json(out/f'mapping_{s}.json',dict(version=f'comparison-{s}-draft',note='Working stratification; not official OECD crosswalk.',groups=[dict(id=g,name=names.get(g,g),rubrics=r) for g,r in sorted(gs.items())],unverified_codes=['99']))
    write_json(out/'comparison_notes.json',dict(common_frame=len(pool),mapped_raw_codes=common_codes,seed=seed,budgets=budgets,period_min=period_min,policies=POLICIES,limitations=['Same restricted frame as V2; no inference to excluded records.','E uses old strict Regex before G exclusion; only proxy, not V2 eligibility.','Six groups are FORD-inspired, not an official mapping or measured OECD data.','V1 top+OTHER uses corrected years and assignment, not historical V1 run.','All proxy variances are exact for observed metadata, not decomposition quality.','Expected coverage sums hypergeometric inclusion probabilities, not actual sampled coverage.','Kish diagnostic does not measure thematic validity. Worst-case binary normal halfwidth is a planning approximation, not a guaranteed 95% CI.','Pareto is descriptive; no automatic choice; fixed current10 reference makes balance criterion favor that definition.','Minimum per nonempty period: 2 by default, except census of size 1.','Hash assignment is conditional on fixed seed, not scientifically primary.']))
    return summaries,failures
