import {ButtonHTMLAttributes,HTMLAttributes,ReactNode} from 'react';
export function Button({className='',...p}:ButtonHTMLAttributes<HTMLButtonElement>){return <button className={`btn ${className}`} {...p}/>}
export function Card({className='',...p}:HTMLAttributes<HTMLDivElement>){return <div className={`card ${className}`} {...p}/>}
export function Badge({children,tone='neutral'}:{children:ReactNode;tone?:'ok'|'warn'|'bad'|'neutral'}){return <span className={`badge ${tone}`}>{children}</span>}
export function Metric({label,value,unit}:{label:string;value:any;unit?:string}){return <Card><div className="metricLabel">{label}</div><div className="metricValue">{value}<span>{unit}</span></div></Card>}
