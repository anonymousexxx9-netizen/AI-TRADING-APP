import React from 'react';
import { Text, View } from 'react-native';
import { s, C } from './ui';

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <View style={{ gap: 10 }}>
    <Text style={[s.label, { color: C.silver }]}>{title}</Text>
    {children}
  </View>;
}

function Row({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return <View style={s.between}>
    <Text style={s.muted}>{label}</Text>
    <Text style={[s.text, { fontWeight: '600', color: color || C.text }]}>{value}</Text>
  </View>;
}

export function MarketAnalysisView({ type, data }: { type: string; data: any }) {
  if (!data) return <Text style={s.muted}>Data tidak tersedia</Text>;

  if (type === 'Ringkasan') {
    return <View style={{ gap: 16 }}>
      <Section title="KONDISI PASAR">
        <Row label="Regime" value={data.regime || '—'} />
        <Row label="Trap Detection" value={data.trap || 'Tidak terdeteksi'} />
      </Section>
    </View>;
  }

  if (type === 'Indikator') {
    const ind = data;
    return <View style={{ gap: 16 }}>
      <Section title="HARGA & WAKTU">
        <Row label="Close" value={ind.close?.toFixed(3) || '—'} color={C.gold} />
        <Row label="Open" value={ind.open?.toFixed(3) || '—'} />
        <Row label="High" value={ind.high?.toFixed(3) || '—'} />
        <Row label="Low" value={ind.low?.toFixed(3) || '—'} />
        <Row label="Waktu" value={ind.datetime || '—'} />
      </Section>
      
      <Section title="MOVING AVERAGES">
        <Row label="EMA 20" value={ind.ema20?.toFixed(2) || '—'} />
        <Row label="EMA 50" value={ind.ema50?.toFixed(2) || '—'} />
        <Row label="EMA 200" value={ind.ema200?.toFixed(2) || '—'} />
      </Section>

      <Section title="OSCILLATORS & MOMENTUM">
        <Row label="RSI" value={ind.rsi?.toFixed(2) || '—'} color={ind.rsi > 70 ? C.red : ind.rsi < 30 ? C.gold : C.text} />
        <Row label="MACD" value={ind.macd?.toFixed(3) || '—'} />
        <Row label="MACD Signal" value={ind.macd_signal?.toFixed(3) || '—'} />
        <Row label="ADX" value={ind.adx?.toFixed(2) || '—'} />
      </Section>

      <Section title="VOLATILITAS">
        <Row label="ATR (Current)" value={ind.atr?.toFixed(2) || '—'} />
        <Row label="ATR (20 ago)" value={ind.atr_20_ago?.toFixed(2) || '—'} />
      </Section>

      <Section title="BOLLINGER BANDS">
        <Row label="Upper" value={ind.bb_upper?.toFixed(2) || '—'} />
        <Row label="Middle" value={ind.bb_middle?.toFixed(2) || '—'} />
        <Row label="Lower" value={ind.bb_lower?.toFixed(2) || '—'} />
      </Section>
    </View>;
  }

  if (type === 'Level S/R') {
    return <View style={{ gap: 16 }}>
      <Section title="HARGA SAAT INI">
        <Row label="Current Price" value={data.current_price?.toFixed(3) || '—'} color={C.gold} />
      </Section>

      <Section title="RESISTANCE LEVELS">
        {data.resistances && data.resistances.length > 0 ? 
          data.resistances.map((r: number, i: number) => 
            <Row key={i} label={`R${i + 1}`} value={r.toFixed(3)} color={C.red} />
          ) : <Text style={s.muted}>Tidak ada resistance</Text>
        }
      </Section>

      <Section title="SUPPORT LEVELS">
        {data.supports && data.supports.length > 0 ? 
          data.supports.map((s: number, i: number) => 
            <Row key={i} label={`S${i + 1}`} value={s.toFixed(3)} color={C.gold} />
          ) : <Text style={s.muted}>Tidak ada support</Text>
        }
      </Section>

      {data.resistance_zones && data.resistance_zones.length > 0 && (
        <Section title="RESISTANCE ZONES">
          {data.resistance_zones.map((zone: any, i: number) => 
            <View key={i} style={{ gap: 4, paddingVertical: 6, borderLeftWidth: 2, borderColor: C.red, paddingLeft: 10 }}>
              <Row label="Level" value={zone.level?.toFixed(3) || '—'} />
              <Row label="Touches" value={zone.touches || 0} />
            </View>
          )}
        </Section>
      )}

      {data.support_zones && data.support_zones.length > 0 && (
        <Section title="SUPPORT ZONES">
          {data.support_zones.map((zone: any, i: number) => 
            <View key={i} style={{ gap: 4, paddingVertical: 6, borderLeftWidth: 2, borderColor: C.gold, paddingLeft: 10 }}>
              <Row label="Level" value={zone.level?.toFixed(3) || '—'} />
              <Row label="Touches" value={zone.touches || 0} />
            </View>
          )}
        </Section>
      )}
    </View>;
  }

  if (type === 'Pattern') {
    return <View style={{ gap: 16 }}>
      <Section title="CANDLESTICK PATTERN">
        <Row label="Pattern" value={data.pattern || 'Tidak terdeteksi'} />
        <Row label="Direction" value={data.direction || '—'} color={data.direction === 'bullish' ? C.gold : data.direction === 'bearish' ? C.red : C.text} />
      </Section>
    </View>;
  }

  if (type === 'Structure') {
    const swingCount = data.swings?.length || 0;
    const lastSwings = data.swings?.slice(-8) || [];
    
    return <View style={{ gap: 16 }}>
      <Section title="MARKET STRUCTURE">
        <Row label="Trend" value={data.trend || '—'} color={data.trend === 'up' ? C.gold : data.trend === 'down' ? C.red : C.text} />
        <Row label="CHoCH" value={data.choch || 'Tidak terdeteksi'} />
        <Row label="Total Swings" value={swingCount} />
      </Section>

      {lastSwings.length > 0 && (
        <Section title="RECENT SWINGS (8 TERAKHIR)">
          {lastSwings.map((swing: any, i: number) => 
            <View key={i} style={{ gap: 4, paddingVertical: 6, borderLeftWidth: 2, borderColor: swing.type === 'high' ? C.red : C.gold, paddingLeft: 10 }}>
              <View style={s.between}>
                <Text style={[s.muted, { fontSize: 11 }]}>#{swing.idx} {swing.type.toUpperCase()}</Text>
                <Text style={[s.text, { fontWeight: '600', fontSize: 13 }]}>{swing.price?.toFixed(3)}</Text>
              </View>
              {swing.label && <Text style={[s.label, { fontSize: 9, color: C.gold }]}>{swing.label}</Text>}
            </View>
          )}
        </Section>
      )}
    </View>;
  }

  if (type === 'Score') {
    return <View style={{ gap: 16 }}>
      <Section title="TECHNICAL SCORE">
        <Row label="Bullish Score" value={`${data.bullish_score || 0} / ${data.max_score || 7}`} color={C.gold} />
        <Row label="Bearish Score" value={`${data.bearish_score || 0} / ${data.max_score || 7}`} color={C.red} />
        <Row label="Confidence" value={`${data.confidence || 0}%`} color={C.gold} />
        <Row label="Edge" value={`${data.edge || 0}%`} />
        <Row label="Bias Direction" value={data.bias_direction || 'Netral'} color={data.bias_direction === 'Bullish' ? C.gold : data.bias_direction === 'Bearish' ? C.red : C.text} />
      </Section>

      {data.detail_factors && data.detail_factors.length > 0 && (
        <Section title="DETAIL FAKTOR">
          {data.detail_factors.map((factor: any, i: number) => 
            <View key={i} style={{ gap: 6, paddingVertical: 8, borderLeftWidth: 2, borderColor: factor.direction === 'bullish' ? C.gold : factor.direction === 'bearish' ? C.red : C.line, paddingLeft: 10 }}>
              <View style={s.between}>
                <Text style={[s.text, { flex: 1, fontWeight: '600', fontSize: 13 }]}>{factor.name}</Text>
                <Text style={[s.label, { fontSize: 10, color: factor.direction === 'bullish' ? C.gold : factor.direction === 'bearish' ? C.red : C.muted }]}>{factor.direction?.toUpperCase()}</Text>
              </View>
              <Text style={[s.muted, { fontSize: 12, lineHeight: 18 }]}>{factor.note}</Text>
            </View>
          )}
        </Section>
      )}

      {data.score_disclaimer && (
        <View style={{ padding: 12, backgroundColor: C.raised, borderRadius: 10 }}>
          <Text style={[s.muted, { fontSize: 11, lineHeight: 16 }]}>{data.score_disclaimer}</Text>
        </View>
      )}
    </View>;
  }

  return <Text style={s.muted}>Tipe analisis tidak dikenali</Text>;
}
