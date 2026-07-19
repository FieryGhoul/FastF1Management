import { useEffect, useRef } from "react";
import { init, use as register, type EChartsCoreOption } from "echarts/core";
import { LineChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

register([
  LineChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
]);

export default function TelemetryChart({
  option,
  height = 520,
}: {
  option: EChartsCoreOption;
  height?: number;
}) {
  const target = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!target.current) return;
    const chart = init(target.current);
    chart.setOption(option);
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [option]);
  return (
    <div ref={target} style={{ height }} aria-label="Motorsport data chart" />
  );
}
