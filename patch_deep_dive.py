import re

with open("streamlit_app.py", "r") as f:
    content = f.read()

new_block = """    elif active_view == "◉  Deep Dive":

        selected = st.session_state.active_ticker
        if not selected:
            st.info("Select a ticker from the sidebar to begin analysis.")
            return

        # Fetch all sections from API
        dcf_data  = fetch_dcf(selected)
        fund_data = fetch_fundamentals(selected)
        narr_data = fetch_narrative(selected)
        full      = fetch_ticker(selected)

        if not dcf_data:
            return

        er = full.get("1_economic_reality", {}) if full else {}
        val4 = full.get("4_valuation_synthesis", {}) if full else {}
        p2v = val4.get("phase2_valuation", {})

        strategic_context = er.get("strategic_context", {})
        contrarian_analysis = val4.get("contrarian_analysis", {})
        adj = p2v.get("dcf_adjustments", {}) or {}
        investment_thesis = val4.get("investment_thesis", {})
        pillar_scores = investment_thesis.get("pillar_scores", {})

        vc = er.get("value_chain", {}) or {}
        moat = er.get("moat", {}) or {}

        # Row from universe
        row = next((r for r in ranked if r["ticker"] == selected), {})

        st.markdown("<br>", unsafe_allow_html=True)

        # Header
        st.markdown(
            f'<div style="font-family:Syne,sans-serif;font-size:36px;'
            f'font-weight:800;letter-spacing:-1px;color:var(--text-color)">{selected}</div>',
            unsafe_allow_html=True,
        )

        m1, m2, m3, m4 = st.columns(4)
        conv = investment_thesis.get("conviction_score") if investment_thesis else None
        m1.metric("Conviction", f"{int(conv):+d} / 10" if conv is not None else "—",
                  delta=row.get("value_creation","").upper() or None)
        m2.metric("Base IV", money(dcf_data["base"]["intrinsic_per_share"]) if dcf_data.get("base") else "—",
                  delta=pct(dcf_data["base"]["margin_of_safety"]) if dcf_data.get("base") else None)
        m3.metric("ROIC", pct(fund_data.get("roic")) if fund_data else "—",
                  delta=f"WACC {pct(dcf_data.get('wacc'))}")
        m4.metric("Multiple Signal", SIGNAL_LABEL.get(row.get("multiple_signal",""), "—"),
                  delta=f"{row.get('ev_ebitda',0):.1f}× vs {row.get('justified_ev_ebitda',0):.1f}× justified"
                  if row.get("ev_ebitda") else None, delta_color="inverse")

        st.markdown("---")

        if pillar_scores:
            p_cols = st.columns(5)
            pillars = [
                ("Moat", pillar_scores.get("p1_moat", 0)),
                ("Health", pillar_scores.get("p2_health", 0)),
                ("Tailwind", pillar_scores.get("p3_tailwind", 0)),
                ("MoS", pillar_scores.get("p4_mos", 0)),
                ("Leadership", pillar_scores.get("p5_leadership", 0)),
            ]
            for i, (name, score) in enumerate(pillars):
                with p_cols[i]:
                    sc_val = int(score) if score is not None else 0
                    sc_color = "#10b981" if sc_val > 3 else "#f59e0b" if sc_val > 1 else "#ef4444"
                    st.markdown(
                        f'<div style="text-align:center;font-family:DM Mono,monospace;font-size:11px;color:#71717a;margin-bottom:4px">{name}</div>'
                        f'<div style="text-align:center;font-weight:700;font-size:18px;color:{sc_color}">{sc_val}</div>',
                        unsafe_allow_html=True
                    )
            st.markdown("<br>", unsafe_allow_html=True)

        left, right = st.columns([1, 1.6])

        with left:
            st.markdown("##### 3-Scenario DCF")
            scenarios = [
                ("BEAR", dcf_data.get("bear", {}), "#ef4444"),
                ("BASE", dcf_data.get("base", {}), "#f59e0b"),
                ("BULL", dcf_data.get("bull", {}), "#10b981"),
            ]
            max_iv = max(
                (s.get("intrinsic_per_share", 0) for _, s, _ in scenarios if s),
                default=1,
            ) * 1.1 or 1

            for name, scenario, color in scenarios:
                if scenario:
                    iv = scenario.get("intrinsic_per_share", 0) or 0
                    pct_width = min(iv / max_iv * 100, 100)
                    mos_val = scenario.get("margin_of_safety")
                    mos_str = f" ({mos_val:+.1%})" if mos_val else ""
                    st.markdown(
                        f'<div style="display:flex;justify-content:space-between;'
                        f'font-family:DM Mono,monospace;font-size:11px;margin-bottom:3px">'
                        f'<span style="color:{color}">{name}</span>'
                        f'<span style="color:#fafafa">${iv:,.0f}{mos_str}</span></div>'
                        f'<div style="background:#27272a;border-radius:3px;height:6px;margin-bottom:10px">'
                        f'<div style="width:{pct_width:.1f}%;height:100%;background:{color};border-radius:3px"></div></div>',
                        unsafe_allow_html=True,
                    )

            st.markdown("<br>", unsafe_allow_html=True)

            # Moat
            moat_score = moat.get("score") or row.get("moat")
            moat_color = "#f59e0b" if moat_score and moat_score >= 9 else "#10b981" if moat_score and moat_score >= 7 else "#ef4444"
            st.markdown("##### Moat")
            st.markdown(
                f'<div style="text-align:center;padding:8px 0">'
                f'<div style="font-family:Syne,sans-serif;font-size:52px;'
                f'font-weight:800;color:{moat_color};line-height:1">'
                f'{moat_score:.1f}</div>'
                f'<div style="font-family:DM Mono,monospace;font-size:10px;'
                f'color:#71717a;margin-top:4px">/ 10  ·  {row.get("value_creation","").upper()}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            
            # Moat Breakdown
            ca = "✅" if moat.get("cost_advantage") else "❌"
            nt = "✅" if moat.get("network_effects") else "❌"
            sw = "✅" if moat.get("switching_costs") else "❌"
            it = "✅" if moat.get("intangibles") else "❌"
            
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;font-family:DM Mono,monospace;font-size:11px;margin-top:8px">'
                f'<span>Cost Adv: {ca}</span><span>Network: {nt}</span>'
                f'<span>Switching: {sw}</span><span>Intangible: {it}</span></div>',
                unsafe_allow_html=True
            )
            if moat.get("evidence"):
                st.markdown(f'<div style="font-size:11px;color:#a1a1aa;margin-top:12px;line-height:1.4"><i>"{moat.get("evidence")}"</i></div>', unsafe_allow_html=True)

            # ROIC vs WACC
            r_roic = fund_data.get("roic") or row.get("roic") or 0
            r_wacc = dcf_data.get("wacc") or row.get("wacc") or 0.09
            spread = r_roic - r_wacc
            st.markdown(
                f'<div style="font-family:DM Mono,monospace;font-size:11px;'
                f'display:flex;flex-direction:column;gap:6px;margin-top:16px">'
                f'<div style="display:flex;justify-content:space-between">'
                f'<span style="color:#71717a">ROIC</span>'
                f'<span style="color:#10b981">{r_roic*100:.1f}%</span></div>'
                f'<div style="display:flex;justify-content:space-between">'
                f'<span style="color:#71717a">WACC</span>'
                f'<span style="color:#fafafa">{r_wacc*100:.1f}%</span></div>'
                f'<div style="display:flex;justify-content:space-between;'
                f'border-top:1px solid #27272a;padding-top:6px">'
                f'<span style="color:#71717a">Spread</span>'
                f'<span style="color:{"#10b981" if spread > 0 else "#ef4444"}">'
                f'{spread*100:+.1f}pp</span></div></div>',
                unsafe_allow_html=True,
            )

            # Value chain
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("##### Value Chain (Porter)")
            st.markdown(
                f'<div style="font-family:DM Mono,monospace;font-size:11px;'
                f'display:flex;flex-direction:column;gap:5px">'
                + (f'<div style="display:flex;justify-content:space-between">'
                   f'<span style="color:#71717a">Strategic Leverage</span>'
                   f'<span>{vc.get("strategic_leverage","—")}</span></div>' if vc.get("strategic_leverage") else "")
                + (f'<div style="display:flex;justify-content:space-between">'
                   f'<span style="color:#71717a">Power Ratio</span>'
                   f'<span>{vc.get("power_ratio","—")}</span></div>' if vc.get("power_ratio") is not None else "")
                + (f'<div style="display:flex;justify-content:space-between">'
                   f'<span style="color:#71717a">Upstream Leak</span>'
                   f'<span style="color:{"#ef4444" if vc.get("upstream_leak") else "#10b981"}">'
                   f'{"YES ⚠" if vc.get("upstream_leak") else "NO ✓"}</span></div>' if vc else "")
                + '</div>',
                unsafe_allow_html=True,
            )
            
            # Strategic Context
            if strategic_context:
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown("##### Strategic Context")
                sc = strategic_context
                
                rev_at_risk = sc.get("revenue_at_risk_percent")
                rev_at_risk_str = f"{rev_at_risk*100:.1f}%" if rev_at_risk is not None else "N/A"
                q_risk = sc.get("quality_of_growth_risk", False)
                def_rev = sc.get("deferred_revenue_trend", "N/A")
                t_haircut = sc.get("terminal_haircut", False)
                
                st.markdown(
                    f'<div style="font-family:DM Mono,monospace;font-size:11px;display:flex;flex-direction:column;gap:5px">'
                    f'<div style="display:flex;justify-content:space-between"><span style="color:#71717a">Rev at Risk</span><span>{rev_at_risk_str}</span></div>'
                    f'<div style="display:flex;justify-content:space-between"><span style="color:#71717a">Quality Risk</span><span style="color:{"#ef4444" if q_risk else "#10b981"}">{q_risk}</span></div>'
                    f'<div style="display:flex;justify-content:space-between"><span style="color:#71717a">Def Rev Trend</span><span>{def_rev}</span></div>'
                    f'<div style="display:flex;justify-content:space-between"><span style="color:#71717a">Terminal Haircut</span><span>{t_haircut}</span></div>'
                    f'</div>',
                    unsafe_allow_html=True
                )
                if sc.get("summary"):
                    st.markdown(f'<div style="font-size:11px;color:#a1a1aa;margin-top:12px;line-height:1.4"><i>"{sc.get("summary")}"</i></div>', unsafe_allow_html=True)


        with right:
            # Fundamentals
            st.markdown("##### Fundamentals — Phase 1 Cleaned")
            if fund_data:
                fm1, fm2, fm3, fm4 = st.columns(4)
                fm1.metric("Revenue", bn(fund_data.get("revenue_bn")))
                fm2.metric("EBITDA",  bn(fund_data.get("ebitda_bn")))
                fm3.metric("FCF",     bn(fund_data.get("fcf_bn")))
                fm4.metric("FCF Margin", pct(fund_data.get("fcf_margin"), 1) if fund_data.get("fcf_margin") else "—")

            st.markdown("<br>", unsafe_allow_html=True)

            # Reverse DCF chart
            st.markdown("##### Reverse DCF — Growth Priced In")
            rdcf = dcf_data.get("reverse_dcf") or {}
            impl  = rdcf.get("implied_cagr_10y") or 0
            hist  = rdcf.get("historical_cagr") or 0

            fig_cagr = go.Figure()
            fig_cagr.add_bar(
                x=["Historical CAGR", "Market Implied CAGR"],
                y=[hist, impl],
                marker_color=["#3b82f6", "#f59e0b"],
                text=[f"{hist:.1%}", f"{impl:.1%}"],
                textposition="outside", textfont=dict(size=11),
            )
            layout_cagr = dict(CHART_LAYOUT)
            layout_cagr.update(
                showlegend=False,
                yaxis=dict(**CHART_LAYOUT.get("yaxis", {}), tickformat=".0%"),
                height=200,
            )
            fig_cagr.update_layout(**layout_cagr)
            st.plotly_chart(fig_cagr, use_container_width=True)

            rdcf_sig = rdcf.get("signal", "")
            ratio_str = f"{impl/hist:.1f}×" if hist and hist > 0 else "N/A"
            st.markdown(
                f'<div style="background:#111113;border:1px solid #27272a;border-radius:6px;'
                f'padding:12px;font-family:DM Mono,monospace;font-size:11px;color:#a1a1aa;margin-top:-8px">'
                f'Market implies <span style="color:#f59e0b">{impl:.1%}</span> CAGR vs '
                f'historical <span style="color:#3b82f6">{hist:.1%}</span> → '
                f'<span style="color:{"#ef4444" if hist > 0 and impl/hist > 2 else "#f59e0b" if hist > 0 and impl/hist > 1.3 else "#10b981"}">'
                f'{ratio_str} historical</span>. '
                + signal_html(rdcf_sig, small=True) + '</div>',
                unsafe_allow_html=True,
            )

            # DCF Adjustments
            if adj:
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown("##### DCF Overrides & Adjustments")
                adj_html = '<div style="background:#111113;border:1px solid #27272a;border-radius:6px;padding:12px;font-family:DM Mono,monospace;font-size:11px;display:flex;flex-direction:column;gap:6px">'
                for k, v in adj.items():
                    if k != "rules" and v is not None:
                        # format value nicely
                        disp_v = f"{v:.4f}" if isinstance(v, float) and v < 1 and v > -1 else f"{v}"
                        adj_html += f'<div style="display:flex;justify-content:space-between"><span style="color:#71717a">{k}</span><span style="color:#fafafa">{disp_v}</span></div>'
                adj_html += '</div>'
                st.markdown(adj_html, unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # Narrative
            narrative = investment_thesis.get("narrative") if investment_thesis else None
            if narrative:
                st.markdown("##### Lead Agent Investment Thesis")
                st.markdown(
                    f'<div style="background:#111113;border:1px solid #27272a;'
                    f'border-radius:8px;padding:16px;font-size:13px;'
                    f'color:#a1a1aa;line-height:1.7">{narrative}</div>',
                    unsafe_allow_html=True,
                )

            # Contrarian View
            if contrarian_analysis:
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown("##### Contrarian Bear Case")
                ca = contrarian_analysis
                st.markdown(
                    f'<div style="background:rgba(239, 68, 68, 0.05);border:1px solid rgba(239, 68, 68, 0.2);'
                    f'border-radius:8px;padding:16px;font-size:12px;'
                    f'color:#fca5a5;line-height:1.6">'
                    f'<div style="margin-bottom:8px"><b>Bias Detected:</b> {ca.get("bias_detected", "None")}</div>'
                    f'<div><b>Bear Case:</b> {ca.get("bear_case_summary", "")}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

"""

pattern = r'    elif active_view == "◉  Deep Dive":.*?    # ──────────────────────────────────────────────────────────────────────────\n    # TAB 3 — SCREENING'
new_content = re.sub(pattern, new_block + '\n    # ──────────────────────────────────────────────────────────────────────────\n    # TAB 3 — SCREENING', content, flags=re.DOTALL)

with open("streamlit_app.py", "w") as f:
    f.write(new_content)

print("Patched successfully!")
