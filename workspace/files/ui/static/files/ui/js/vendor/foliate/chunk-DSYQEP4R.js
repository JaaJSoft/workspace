var N=d=>d?d.replace(/[\t\n\f\r ]+/g," ").replace(/^[\t\n\f\r ]+/,"").replace(/[\t\n\f\r ]+$/,""):"",u=d=>N(d?.textContent),S={XLINK:"http://www.w3.org/1999/xlink",EPUB:"http://www.idpf.org/2007/ops"},T={XML:"application/xml",XHTML:"application/xhtml+xml"},l={strong:["strong","self"],emphasis:["em","self"],style:["span","self"],a:"anchor",strikethrough:["s","self"],sub:["sub","self"],sup:["sup","self"],code:["code","self"],image:"image"},E={tr:["tr",{th:["th",l,["colspan","rowspan","align","valign"]],td:["td",l,["colspan","rowspan","align","valign"]]},["align"]]},k={epigraph:["blockquote"],subtitle:["h2",l],"text-author":["p",l],date:["p",l],stanza:"stanza"},L={title:["header",{p:["h1",l],"empty-line":["br"]}],epigraph:["blockquote","self"],image:"image",annotation:["aside"],section:["section","self"],p:["p",l],poem:["blockquote",k],subtitle:["h2",l],cite:["blockquote","self"],"empty-line":["br"],table:["table",E],"text-author":["p",l]};k.epigraph.push(L);var D={image:"image",title:["section",{p:["h1",l],"empty-line":["br"]}],epigraph:["section",L],section:["section",L]},q=class{constructor(t){this.fb2=t,this.doc=document.implementation.createDocument(S.XHTML,"html"),this.bins=new Map(Array.from(this.fb2.getElementsByTagName("binary"),n=>[n.id,n]))}getImageSrc(t){let n=t.getAttributeNS(S.XLINK,"href");if(!n)return"data:,";let[,a]=n.split("#");if(!a)return n;let s=this.bins.get(a);return s?`data:${s.getAttribute("content-type")};base64,${s.textContent}`:n}image(t){let n=this.doc.createElement("img");return n.alt=t.getAttribute("alt"),n.title=t.getAttribute("title"),n.setAttribute("src",this.getImageSrc(t)),n}anchor(t){let n=this.convert(t,{a:["a",l]});return n.setAttribute("href",t.getAttributeNS(S.XLINK,"href")),t.getAttribute("type")==="note"&&n.setAttributeNS(S.EPUB,"epub:type","noteref"),n}stanza(t){let n=this.convert(t,{stanza:["p",{title:["header",{p:["strong",l],"empty-line":["br"]}],subtitle:["p",l]}]});for(let a of t.children)a.nodeName==="v"&&(n.append(this.doc.createTextNode(a.textContent)),n.append(this.doc.createElement("br")));return n}convert(t,n){if(t.nodeType===3)return this.doc.createTextNode(t.textContent);if(t.nodeType===4)return this.doc.createCDATASection(t.textContent);if(t.nodeType===8)return this.doc.createComment(t.textContent);let a=n?.[t.nodeName];if(!a)return null;if(typeof a=="string")return this[a](t);let[s,p,m]=a,f=this.doc.createElement(s);if(t.id&&(f.id=t.id),f.classList.add(t.nodeName),Array.isArray(m))for(let g of m){let A=t.getAttribute(g);A&&f.setAttribute(g,A)}let w=p==="self"?n:p,y=t.firstChild;for(;y;){let g=this.convert(y,w);g&&f.append(g),y=y.nextSibling}return f}},$=async d=>{let t=await d.arrayBuffer(),n=new TextDecoder("utf-8").decode(t),a=new DOMParser,s=a.parseFromString(n,T.XML),p=s.xmlEncoding||n.match(/^<\?xml\s+version\s*=\s*["']1.\d+"\s+encoding\s*=\s*["']([A-Za-z0-9._-]*)["']/)?.[1];if(p&&p.toLowerCase()!=="utf-8"){let m=new TextDecoder(p).decode(t);return a.parseFromString(m,T.XML)}return s},O=URL.createObjectURL(new Blob([`
@namespace epub "http://www.idpf.org/2007/ops";
body > img, section > img {
    display: block;
    margin: auto;
}
.title h1 {
    text-align: center;
}
body > section > .title, body.notesBodyType > .title {
    margin: 3em 0;
}
body.notesBodyType > section .title h1 {
    text-align: start;
}
body.notesBodyType > section .title {
    margin: 1em 0;
}
p {
    text-indent: 1em;
    margin: 0;
}
:not(p) + p, p:first-child {
    text-indent: 0;
}
.poem p {
    text-indent: 0;
    margin: 1em 0;
}
.text-author, .date {
    text-align: end;
}
.text-author:before {
    content: "\u2014";
}
table {
    border-collapse: collapse;
}
td, th {
    padding: .25em;
}
a[epub|type~="noteref"] {
    font-size: .75em;
    vertical-align: super;
}
body:not(.notesBodyType) > .title, body:not(.notesBodyType) > .epigraph {
    margin: 3em 0;
}
`],{type:"text/css"})),X=d=>`<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
    <head><link href="${O}" rel="stylesheet" type="text/css"/></head>
    <body>${d}</body>
</html>`,M="data-foliate-id",z=async d=>{let t={},n=await $(d),a=new q(n),s=e=>n.querySelector(e),p=e=>[...n.querySelectorAll(e)],m=e=>{let o=u(e.querySelector("nickname"));if(o)return o;let r=u(e.querySelector("first-name")),i=u(e.querySelector("middle-name")),c=u(e.querySelector("last-name")),h=[r,i,c].filter(b=>b).join(" "),x=c?[c,[r,i].filter(b=>b).join(" ")].join(", "):null;return{name:h,sortAs:x}},f=e=>e?.getAttribute("value")??u(e),w=s("title-info annotation");if(t.metadata={title:u(s("title-info book-title")),identifier:u(s("document-info id")),language:u(s("title-info lang")),author:p("title-info author").map(m),translator:p("title-info translator").map(m),contributor:p("document-info author").map(m).concat(p("document-info program-used").map(u)).map(e=>Object.assign(typeof e=="string"?{name:e}:e,{role:"bkp"})),publisher:u(s("publish-info publisher")),published:f(s("title-info date")),modified:f(s("document-info date")),description:w?a.convert(w,{annotation:["div",L]}).innerHTML:null,subject:p("title-info genre").map(u)},s("coverpage image")){let e=a.getImageSrc(s("coverpage image"));t.getCover=()=>fetch(e).then(o=>o.blob())}else t.getCover=()=>null;let y=Array.from(n.querySelectorAll("body"),e=>{let o=a.convert(e,{body:["body",D]});return[Array.from(o.children,r=>{let i=[r,...r.querySelectorAll("[id]")].map(c=>c.id);return{el:r,ids:i}}),o]}),g=[],A=y[0][0].map(({el:e,ids:o})=>{let r=Array.from(e.querySelectorAll(":scope > section > .title"),(i,c)=>(i.setAttribute(M,c),{title:u(i),index:c}));return{ids:o,titles:r,el:e}}).concat(y.slice(1).map(([e,o])=>{let r=e.map(i=>i.ids).flat();return o.classList.add("notesBodyType"),{ids:r,el:o,linear:"no"}})).map(({ids:e,titles:o,el:r,linear:i})=>{let c=X(r.outerHTML),h=new Blob([c],{type:T.XHTML}),x=URL.createObjectURL(h);g.push(x);let b=N(r.querySelector(".title, .subtitle, p")?.textContent??(r.classList.contains("title")?r.textContent:""));return{ids:e,title:b,titles:o,load:()=>x,createDocument:()=>new DOMParser().parseFromString(c,T.XHTML),size:h.size-Array.from(r.querySelectorAll("[src]"),v=>v.getAttribute("src")?.length??0).reduce((v,B)=>v+B,0),linear:i}}),C=new Map;return t.sections=A.map((e,o)=>{let{ids:r,load:i,createDocument:c,size:h,linear:x}=e;for(let b of r)b&&C.set(b,o);return{id:o,load:i,createDocument:c,size:h,linear:x}}),t.toc=A.map(({title:e,titles:o},r)=>{let i=r.toString();return{label:e,href:i,subitems:o?.length?o.map(({title:c,index:h})=>({label:c,href:`${i}#${h}`})):null}}).filter(e=>e),t.resolveHref=e=>{let[o,r]=e.split("#");return o?{index:Number(o),anchor:i=>i.querySelector(`[${M}="${r}"]`)}:{index:C.get(r),anchor:i=>i.getElementById(r)}},t.splitTOCHref=e=>e?.split("#")?.map(o=>Number(o))??[],t.getTOCFragment=(e,o)=>e.querySelector(`[${M}="${o}"]`),t.destroy=()=>{for(let e of g)URL.revokeObjectURL(e)},t};export{z as makeFB2};
