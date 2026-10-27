// Собрано из starter/Калькулятор ЭХЗ.html (tools/port_calculator.py). Не править руками.
(() => {
  'use strict';
  const $=id=>document.getElementById(id), num=id=>Number($(id).value), fmt=(v,d=5)=>Number.isFinite(v)?v.toLocaleString('ru-RU',{maximumFractionDigits:d}):'—';
  const sci=v=>Number.isFinite(v)?v.toLocaleString('ru-RU',{useGrouping:false,maximumFractionDigits:12}):'—';
  const state={rNorm:NaN,rt:NaN,integral:null,measured:null,band:null,allowable:null,corrosion:null};
  const methods={
    rg:[['pe','Двух-/трёхслойное ПЭ или ПП',3e5,.05],['thermo','Термореактивное полимерное / битумно-полимерная мастика',1e5,.08],['mastic','Мастичное, полимерно-битумное усиленное / нормального типа',5e4,.11]],
    gost58094:[['bitumen','Битумное или полимерное ленточное',1e5,.125],['r51164','Покрытие по ГОСТ Р 51164',3e5,.104],['extruded','Экструдированный полиэтилен по ГОСТ Р 55436',3e5,.084]],
    custom:[['custom','Пользовательское покрытие',1e5,.1]]
  };
  const diameters=[57,76,89,108,114,133,146,152,159,168,219,273,325,377,426,530,630,720,820,1020,1220,1420];
  const walls=[3,3.5,4,4.5,5,5.5,6,6.5,7,7.5,8,8.5,9,9.5,10,10.5,11,11.5,12,12.5,14,15,16,17,20];
  function showError(id,msg){const e=$(id);e.textContent=msg||'';e.style.display=msg?'block':'none'}
  function positive(v,name){if(!Number.isFinite(v)||v<=0)throw Error(name+' должно быть больше нуля');return v}
  function base(){const D=positive(num('diameter'),'Диаметр')/1000,d=positive(num('thickness'),'Толщина')/1000,rho=positive(num('steelRho'),'ρ стали');if(d>=D/2)throw Error('Толщина стенки должна быть меньше радиуса трубы');const area=Math.PI*(D-d)*d,rt=rho/area;return{D,d,rho,area,rt,H:positive(num('depth'),'Глубина')}}
  function updateCoatings(){const list=methods[$('normMethod').value];$('coating').innerHTML=list.map(x=>`<option value="${x[0]}">${x[1]}</option>`).join('');applyCoating()}
  function applyCoating(){const x=methods[$('normMethod').value].find(x=>x[0]===$('coating').value)||methods[$('normMethod').value][0];$('r0').value=x[2];$('gamma').value=x[3];const custom=$('normMethod').value==='custom';$('r0').readOnly=!custom;$('gamma').readOnly=!custom}
  function calcNorm(){try{showError('normError','');const y0=num('yearStart'),ys=num('yearSurvey');if(!Number.isInteger(y0)||!Number.isInteger(ys)||ys<y0)throw Error('Проверьте годы ввода и обследования');const age=ys-y0,r0=positive(num('r0'),'Rиз0'),g=positive(num('gamma'),'γ'),lim=positive(num('rLimit'),'Предельное сопротивление');state.rNorm=r0*Math.exp(-g*age);const life=Math.log(r0/lim)/g,remaining=life-age;$('ageOut').textContent=fmt(age,0)+' лет';$('rNormOut').textContent=sci(state.rNorm)+' Ом·м²';$('lifeOut').textContent=fmt(life,1)+' лет';$('remainingOut').innerHTML=remaining>=0?`<span class="status ok">${fmt(remaining,1)} лет</span>`:`<span class="status bad">предел превышен на ${fmt(-remaining,1)} лет</span>`;updateReport();return state.rNorm}catch(e){showError('normError',e.message);state.rNorm=NaN;return NaN}}
  function calcLong(){try{showError('longError','');const b=base();state.rt=b.rt;$('rtOut').textContent=sci(b.rt)+' Ом/м';$('rtMicroOut').textContent=fmt(b.rt*1e6,3);$('areaOut').textContent=fmt(b.area*1e6,2)+' мм²';const Dmm=b.D*1000,dmm=b.d*1000,nd=diameters.reduce((a,x)=>Math.abs(x-Dmm)<Math.abs(a-Dmm)?x:a),nw=walls.reduce((a,x)=>Math.abs(x-dmm)<Math.abs(a-dmm)?x:a);$('nearestOut').textContent=`D ${nd} × ${nw} мм`;buildLongTable(b.rho,Dmm,dmm);updateReport();return b.rt}catch(e){showError('longError',e.message);state.rt=NaN;return NaN}}
  function buildLongTable(rho,selD,selW){let html='<thead><tr><th>D, мм</th>'+walls.map(w=>`<th>${w}</th>`).join('')+'</tr></thead><tbody>';for(const D of diameters){html+='<tr><td>'+D+'</td>';for(const w of walls){if(w>=D/2){html+='<td>—</td>';continue}const rt=rho/(Math.PI*((D-w)/1000)*(w/1000))*1e6;const hit=Math.abs(D-selD)<.01&&Math.abs(w-selW)<.01;html+=`<td${hit?' class="hit"':''}>${fmt(rt,2)}</td>`}html+='</tr>'}$('longTable').innerHTML=html+'</tbody>'}
  function parseSeries(text){const rows=[];for(const [i,line] of text.split(/\r?\n/).entries()){const s=line.trim();if(!s||s.startsWith('#'))continue;const raw=s.includes(';')||s.includes('\t')?s.split(/[;\t]+/):s.split(/\s+/);const p=raw.map(x=>Number(x.trim().replace(',','.')));if(p.length<2||p.some(x=>!Number.isFinite(x)))throw Error(`Ошибка данных в строке ${i+1}: ${line}`);const x=p[0],u=p.length>=3?Math.abs(p[1]-p[2]):Math.abs(p[1]);rows.push({x,u})}rows.sort((a,b)=>a.x-b.x);if(rows.length<2)throw Error('Для каждой ветви нужно не менее двух точек');if(rows[0].x!==0){const x0=rows[0].x;rows.forEach(r=>r.x-=x0)}for(let i=1;i<rows.length;i++)if(rows[i].x<=rows[i-1].x)throw Error('Расстояния должны быть уникальными и возрастать');return rows}
  function integrate(rows){let s=0;for(let i=1;i<rows.length;i++)s+=(rows[i].x-rows[i-1].x)*(rows[i].u+rows[i-1].u)/2;return{s,L:rows.at(-1).x}}
  function solveAlpha(I,rt,left,right,tol,maxIter){
    const CL=I*rt/left.s,CR=I*rt/right.s;
    function branchAlpha(C,L,sum){
      if(C*L<=sum)return 0;
      let lo=Number.EPSILON,hi=C/sum;
      for(let j=0;j<80;j++){
        const mid=(lo+hi)/2,g=C*(-Math.expm1(-mid*L))/sum-mid;
        if(g>0)lo=mid;else hi=mid;
      }
      return(lo+hi)/2
    }
    function at(sum){const aL=branchAlpha(CL,left.L,sum),aR=branchAlpha(CR,right.L,sum);return{aL,aR,h:aL+aR-sum}}
    let lo=Math.min(CL*left.L,CR*right.L)*1e-10,hi=Math.min(CL*left.L,CR*right.L)*(1-1e-12),cur=at((lo+hi)/2),err=Infinity,k=0;
    if(!(at(lo).h>0&&at(hi).h<0))throw Error('Для введённых данных не удалось ограничить область сходимости');
    for(k=1;k<=maxIter;k++){
      const mid=(lo+hi)/2;cur=at(mid);err=(hi-lo)/Math.max(mid,Number.EPSILON);
      if(Math.abs(cur.h)/Math.max(mid,Number.EPSILON)<tol||err<tol)return{aL:cur.aL,aR:cur.aR,k,err:Math.max(err,Math.abs(cur.h)/mid),converged:true};
      if(cur.h>0)lo=mid;else hi=mid
    }
    return{aL:cur.aL,aR:cur.aR,k:maxIter,err,converged:false}
  }
  function weightedMeanResistance(rL,rR,LL,LR){return Math.pow((LL+LR)/(LL/Math.sqrt(rL)+LR/Math.sqrt(rR)),2)}
  function badge(text,kind){return `<span class="status ${kind}">${text}</span>`}
  function assess(r){if(!Number.isFinite(r))return'—';const norm=state.rNorm,lim=num('rLimit');if(r<lim)return badge('ниже предела','bad');if(Number.isFinite(norm)&&r<norm)return badge('ниже норматива','warn');return badge('соответствует','ok')}
  function calcIntegral(){try{showError('integralError','');const b=base();state.rt=b.rt;if(!Number.isFinite(state.rNorm))calcNorm();const I=positive(num('drainCurrent'),'Ток'),tol=positive(num('tolerance'),'Точность'),maxIter=Math.trunc(positive(num('maxIter'),'Число итераций')),lRows=parseSeries($('leftData').value),rRows=parseSeries($('rightData').value),L=integrate(lRows),R=integrate(rRows);positive(L.s,'Интеграл левой ветви');positive(R.s,'Интеграл правой ветви');positive(L.L,'Длина левой ветви');positive(R.L,'Длина правой ветви');const sol=solveAlpha(I,b.rt,L,R,tol,maxIter);if(!sol.converged)throw Error(`Нет сходимости за ${maxIter} итераций; достигнутая относительная невязка ${sci(sol.err)}`);const rpl=Math.PI*b.D*b.rt/(sol.aL**2),rpr=Math.PI*b.D*b.rt/(sol.aR**2),avg=weightedMeanResistance(rpl,rpr,L.L,R.L);state.integral={...sol,SL:L.s,SP:R.s,LL:L.L,LP:R.L,rpl,rpr,avg};$('sOut').textContent=`${fmt(L.s,2)} / ${fmt(R.s,2)}`;$('alphaOut').textContent=`${sci(sol.aL)} / ${sci(sol.aR)}`;$('rplOut').textContent=sci(rpl)+' Ом·м²';$('rprOut').textContent=sci(rpr)+' Ом·м²';$('rpAvgOut').textContent=sci(avg)+' Ом·м²';$('convOut').innerHTML=badge(`${sol.k} ит.; ε=${sci(sol.err)}`,'ok');$('integralStatus').innerHTML=Number.isFinite(state.rNorm)?(avg>=state.rNorm?badge('Rп ≥ Rнорм','ok'):badge('Rп < Rнорм','warn')):'—';$('limitStatus').innerHTML=assess(avg);drawChart(lRows,rRows);updateReport();return state.integral}catch(e){showError('integralError',e.message);state.integral=null;return null}}
  function drawChart(left,right){const c=$('chart'),ctx=c.getContext('2d'),w=c.width,h=c.height,p=38,maxX=Math.max(left.at(-1).x,right.at(-1).x),maxU=Math.max(...left.map(x=>x.u),...right.map(x=>x.u))*1.12;ctx.clearRect(0,0,w,h);ctx.strokeStyle='#cad7df';ctx.lineWidth=1;for(let i=0;i<=5;i++){const y=p+(h-2*p)*i/5;ctx.beginPath();ctx.moveTo(p,y);ctx.lineTo(w-p,y);ctx.stroke()}ctx.strokeStyle='#405865';ctx.beginPath();ctx.moveTo(p,p);ctx.lineTo(p,h-p);ctx.lineTo(w-p,h-p);ctx.stroke();function line(rows,color){ctx.strokeStyle=color;ctx.lineWidth=3;ctx.beginPath();rows.forEach((r,i)=>{const x=p+r.x/maxX*(w-2*p),y=h-p-r.u/maxU*(h-2*p);i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke()}line(left,'#087f8c');line(right,'#e0792d');ctx.fillStyle='#28434e';ctx.font='13px Segoe UI';ctx.fillText('ΔU, В',5,18);ctx.fillText('L, м',w-34,h-8);ctx.fillStyle='#087f8c';ctx.fillText('левая',w-160,20);ctx.fillStyle='#e0792d';ctx.fillText('правая',w-90,20)}
  function calcMeasured(){try{showError('measuredError','');const b=base(),rp=positive(num('measuredRp'),'Rп'),mode=$('spreadMode').value;state.rt=b.rt;if(!Number.isFinite(state.rNorm))calcNorm();let spread=0;if(mode==='manual')spread=Math.max(0,num('manualSpread'));else if(mode==='calc'){const soil=positive(num('soilRho'),'ρ грунта'),arg=.4*rp/(b.D*b.D*b.H*b.rt);if(arg<=0)throw Error('Аргумент логарифма недопустим');spread=soil*b.D/2*Math.log(arg);if(spread<0)throw Error('Получено отрицательное Rр: проверьте Rп, глубину и геометрию')}const rIso=rp-spread;if(rIso<=0)throw Error('Rиз ≤ 0: исходные данные физически несогласованы');state.measured={rp,spread,rIso};$('spreadOut').textContent=sci(spread)+' Ом·м²';$('rIsoOut').textContent=sci(rIso)+' Ом·м²';$('ratioOut').textContent=Number.isFinite(state.rNorm)?fmt(rIso/state.rNorm,3):'—';$('measuredStatus').innerHTML=assess(rIso);updateReport();return state.measured}catch(e){showError('measuredError',e.message);state.measured=null;return null}}
  function calcBand(){try{showError('bandError','');const b=base(),U=positive(num('bandU'),'Напряжение'),i1=positive(num('bandI1'),'I1'),i2=Math.max(0,num('bandI2')),bw=positive(num('bandWidth'),'Ширина бандажа');if(i1<=i2)throw Error('I1 должно быть больше I2');if(!Number.isFinite(state.rNorm))calcNorm();const area=Math.PI*b.D*bw,r=U*area/(i1-i2);state.band={area,r};$('bandAreaOut').textContent=fmt(area,4)+' м²';$('bandROut').textContent=sci(r)+' Ом·м²';$('bandRatioOut').textContent=Number.isFinite(state.rNorm)?fmt(r/state.rNorm,3):'—';$('bandStatus').innerHTML=assess(r);updateReport();return state.band}catch(e){showError('bandError',e.message);state.band=null;return null}}
  function calcAllowThickness(applyToForecast=true){
    try{
      showError('allowError','');
      const D=positive(num('diameter'),'Наружный диаметр'),DN=positive(num('allowDN'),'DN'),p=positive(num('allowPressure'),'Рабочее давление'),n=positive(num('allowN'),'Коэффициент n'),R1n=positive(num('allowR1n'),'R₁н'),m=positive(num('allowM'),'Коэффициент m'),k1=positive(num('allowK1'),'Коэффициент k₁'),kn=positive(num('allowKn'),'Коэффициент kн'),addition=Math.max(0,num('allowAddition'));
      const R1=R1n*m/(k1*kn),pressure=n*p*D/(2*(R1+n*p)),dnFloor=DN/100,constructFloor=DN>=1000?12:(DN<=200?3:4),method=$('allowMethod').value;
      const baseLimit=method==='sp36'?Math.max(pressure,dnFloor,constructFloor):pressure,final=baseLimit+addition,current=positive(num('corCurrentThk'),'Текущая минимальная толщина'),margin=current-final;
      if(final>=D/2)throw Error('Расчётная толщина получилась не меньше радиуса трубы: проверьте исходные данные');
      state.allowable={method,D,DN,p,n,R1n,m,k1,kn,addition,R1,pressure,dnFloor,constructFloor,final,margin};
      $('allowR1Out').textContent=fmt(R1,3)+' МПа';$('allowPressureOut').textContent=fmt(pressure,3)+' мм';$('allowDnOut').textContent=fmt(dnFloor,3)+' мм';$('allowConstructOut').textContent=fmt(constructFloor,3)+' мм';$('allowFinalOut').innerHTML=badge(fmt(final,3)+' мм',margin>=0?'ok':'bad');$('allowMarginOut').innerHTML=margin>=0?badge(fmt(margin,3)+' мм','ok'):badge('не хватает '+fmt(-margin,3)+' мм','bad');
      if(applyToForecast)$('corAllowThk').value=final.toFixed(6).replace(/0+$/,'').replace(/\.$/,'');
      updateReport();return state.allowable
    }catch(e){showError('allowError',e.message);state.allowable=null;return null}
  }
  function calcCorrosion(){
    try{
      showError('corrosionError','');
      const nominal=positive(num('thickness'),'Номинальная толщина'),current=positive(num('corCurrentThk'),'Текущая толщина'),allow=positive(num('corAllowThk'),'Допустимая толщина'),depth=Math.max(0,num('corDepthNow')),depthAllow=positive(num('corDepthAllow'),'Допустимая глубина'),horizon=Math.max(0,num('corHorizon')),y0=num('yearStart'),ys=num('yearSurvey'),age=ys-y0;
      if(!(age>0))throw Error('Для прогноза год обследования должен быть позже года ввода');
      if(allow>nominal)throw Error('Минимально допустимая толщина не может превышать номинальную');
      if(current>nominal)throw Error('Текущая минимальная толщина не может превышать номинальную');
      const prevYear=num('corPrevYear'),dt=ys-prevYear,prevThk=num('corPrevThk'),prevDepth=num('corPrevDepth');
      const vService=Math.max(0,(nominal-current)/age);
      const vBetween=dt>0&&prevThk>=current?(prevThk-current)/dt:NaN;
      const vDepth=dt>0&&depth>=prevDepth?(depth-prevDepth)/dt:NaN;
      const loss=Math.max(0,num('couponLoss')),area=num('couponArea'),hours=num('couponHours'),density=num('couponDensity');
      const vCoupon=loss>0&&area>0&&hours>0&&density>0?8.76e4*loss/(area*hours*density):NaN;
      const vDirect=Math.max(0,num('corDirect'));
      const rates=[vService,vBetween,vDepth,vCoupon,vDirect].filter(v=>Number.isFinite(v)&&v>0);
      if(!rates.length)throw Error('Ни одним методом не получена положительная скорость коррозии');
      const accepted=Math.max(...rates),lifeThk=current<=allow?0:(current-allow)/accepted,lifeDepth=depth>=depthAllow?0:(depthAllow-depth)/accepted;
      const pitSize=Math.max(0,num('pitSize')),pressure=Math.max(0,num('pitPressure')),yieldStrength=positive(num('pitYield'),'Предел текучести'),sigmaMax=.75*yieldStrength;
      let hCrit=NaN,vPit=NaN,lifePit=NaN;
      if(pitSize>0){hCrit=nominal-pitSize*Math.sqrt(.19*pressure/sigmaMax);const vPlane=pitSize/age,vCor=vService>0?vService:accepted;vPit=(vCor+vPlane)/2;lifePit=depth>=hCrit?0:(hCrit-depth)/vPit}
      const lifeCandidates=[lifeThk,lifeDepth,lifePit].filter(v=>Number.isFinite(v)&&v>=0),lifeMin=Math.min(...lifeCandidates),limitYear=ys+lifeMin;
      const forecastThk=Math.max(0,current-accepted*horizon),forecastDepth=depth+accepted*horizon;
      state.corrosion={nominal,current,allow,depth,depthAllow,horizon,vService,vBetween,vDepth,vCoupon,vDirect,accepted,lifeThk,lifeDepth,hCrit,vPit,lifePit,lifeMin,limitYear,forecastThk,forecastDepth};
      const rateText=v=>Number.isFinite(v)?v.toLocaleString('ru-RU',{useGrouping:false,maximumFractionDigits:6})+' мм/год':'нет данных',lifeText=v=>Number.isFinite(v)?fmt(Math.max(0,v),2)+' лет':'не рассчитан';
      $('corVService').textContent=rateText(vService);$('corVBetween').textContent=rateText(vBetween);$('corVDepth').textContent=rateText(vDepth);$('corVCoupon').textContent=rateText(vCoupon);$('corVAccepted').innerHTML=badge(rateText(accepted),'warn');$('corLifeThk').textContent=lifeText(lifeThk);$('corLifeDepth').textContent=lifeText(lifeDepth);$('corLifePit').textContent=lifeText(lifePit);$('corForecastThk').textContent=fmt(forecastThk,4)+' мм';$('corForecastDepth').textContent=fmt(forecastDepth,4)+' мм';$('corLifeMin').innerHTML=lifeMin>0?badge(lifeText(lifeMin),'warn'):badge('предел достигнут','bad');$('corLimitYear').textContent=limitYear.toLocaleString('ru-RU',{useGrouping:false,maximumFractionDigits:2});
      updateReport();buildCorrosionProtocol();return state.corrosion
    }catch(e){showError('corrosionError',e.message);state.corrosion=null;return null}
  }
  function buildCorrosionProtocol(){
    const a=state.allowable,c=state.corrosion,out=$('corrosionProtocol');
    if(!c){out.textContent='Коррозионный прогноз не рассчитан. Проверьте исходные данные и выполните расчёт.';return''}
    const v=(x,d=6)=>Number.isFinite(x)?x.toLocaleString('ru-RU',{useGrouping:false,maximumFractionDigits:d}):'нет данных';
    const y0=num('yearStart'),ys=num('yearSurvey'),age=ys-y0,prevYear=num('corPrevYear'),dt=ys-prevYear,prevThk=num('corPrevThk'),prevDepth=num('corPrevDepth'),direct=num('corDirect'),loss=num('couponLoss'),area=num('couponArea'),hours=num('couponHours'),density=num('couponDensity'),pitSize=num('pitSize'),pitPressure=num('pitPressure'),pitYield=num('pitYield');
    const lines=[
      'ПРОТОКОЛ РАСЧЁТА МИНИМАЛЬНО ДОПУСТИМОЙ ТОЛЩИНЫ И КОРРОЗИОННОГО ПРОГНОЗА',
      '',
      `Объект: ${$('name').value}`,
      `Год ввода в эксплуатацию: ${y0}`,
      `Год обследования: ${ys}`,
      `Продолжительность эксплуатации: ${v(age,2)} лет`,
      `Наружный диаметр трубы D: ${v(num('diameter'),3)} мм`,
      `Номинальная толщина стенки δ0: ${v(num('thickness'),3)} мм`,
      `Минимальная фактическая толщина δф: ${v(c.current,3)} мм`,
      `Фактическая глубина дефекта cф: ${v(c.depth,3)} мм`,
      '',
      '1. РАСЧЁТ МИНИМАЛЬНО ДОПУСТИМОЙ ТОЛЩИНЫ'
    ];
    if(a){
      lines.push(
        `Режим: ${a.method==='sp36'?'СП 36.13330 с конструктивными минимумами по DN':'эксплуатационная оценка прямой трубы по внутреннему давлению'}`,
        `Исходные данные: p = ${v(a.p,3)} МПа; DN = ${v(a.DN,0)} мм; n = ${v(a.n,3)}; R1н = ${v(a.R1n,3)} МПа; m = ${v(a.m,3)}; k1 = ${v(a.k1,3)}; kн = ${v(a.kn,3)}; C = ${v(a.addition,3)} мм.`,
        '',
        'Происхождение характеристик и коэффициентов:',
        'R1н — минимальное нормативное временное сопротивление металла растяжению по сертификату трубы либо стандарту/ТУ на трубу (СП 36.13330, п. 12.1.1).',
        'm — коэффициент условий работы по категории участка, таблица 1 СП 36: В — 0,660; I–II — 0,825; III–IV — 0,990.',
        'k1 — коэффициент надёжности по материалу по способу изготовления и объёму контроля трубы, таблица 10 СП 36: 1,34; 1,40; 1,47 или 1,55.',
        'kн — коэффициент надёжности по ответственности, выбираемый по DN и рабочему давлению, таблица 12 СП 36.',
        'n — коэффициент надёжности по нагрузке от внутреннего давления, таблица 14 СП 36; для газопроводов обычно 1,10.',
        'C — дополнительная суммарная прибавка. Она не входит в формулу (10) СП 36 и применяется только при наличии требования проекта или другого применимого документа. Может учитывать минусовый допуск заготовки, технологическое утонение и запас на коррозию/эрозию.',
        'При отдельном прогнозе утонения прибавку на будущую коррозию нельзя учитывать повторно без обоснования, поскольку это может привести к двойному учёту одного износа.',
        '',
        `Электрическое Rнорм покрытия = ${sci(state.rNorm)} Ом·м² — отдельная характеристика изоляции. Она не является R1н металла и не входит в расчёт толщины стенки.`,
        '',
        'Расчётное сопротивление растяжению:',
        `R1 = R1н·m/(k1·kн) = ${v(a.R1n,3)}·${v(a.m,3)}/(${v(a.k1,3)}·${v(a.kn,3)}) = ${v(a.R1,3)} МПа.`,
        '',
        'Толщина, требуемая по внутреннему давлению:',
        `δp = n·p·D/[2(R1+n·p)] = ${v(a.n,3)}·${v(a.p,3)}·${v(a.D,3)}/[2·(${v(a.R1,3)}+${v(a.n,3)}·${v(a.p,3)})] = ${v(a.pressure,3)} мм.`
      );
      if(a.method==='sp36')lines.push(`Минимум 1/100 DN: ${v(a.dnFloor,3)} мм.`,`Конструктивный минимум для DN ${v(a.DN,0)}: ${v(a.constructFloor,3)} мм.`,`Итог: δдоп = max(${v(a.pressure,3)}; ${v(a.dnFloor,3)}; ${v(a.constructFloor,3)}) + ${v(a.addition,3)} = ${v(a.final,3)} мм.`);
      else lines.push(`Итог: δдоп = δp + C = ${v(a.pressure,3)} + ${v(a.addition,3)} = ${v(a.final,3)} мм.`);
      lines.push(`Запас фактической толщины: δф − δдоп = ${v(c.current,3)} − ${v(a.final,3)} = ${v(c.current-a.final,3)} мм.`);
      if(Math.abs(c.allow-a.final)>1e-6)lines.push(`Примечание: в коррозионном прогнозе использовано отдельно введённое значение δдоп = ${v(c.allow,3)} мм, отличающееся от полученного расчётом.`);
    }else lines.push(`Минимально допустимая толщина принята вручную: ${v(c.allow,3)} мм.`);
    lines.push('', '2. ОПРЕДЕЛЕНИЕ СКОРОСТИ КОРРОЗИИ');
    lines.push(`За весь срок эксплуатации: Vэкспл = (δ0−δф)/t = (${v(c.nominal,3)}−${v(c.current,3)})/${v(age,2)} = ${v(c.vService)} мм/год.`);
    if(Number.isFinite(c.vBetween))lines.push(`Между обследованиями: Vмеж = (δпред−δф)/Δt = (${v(prevThk,3)}−${v(c.current,3)})/${v(dt,2)} = ${v(c.vBetween)} мм/год.`);else lines.push('Между обследованиями: не рассчитано — недостаточно согласованных исходных данных.');
    if(Number.isFinite(c.vDepth))lines.push(`По росту дефекта: Vдеф = (cф−cпред)/Δt = (${v(c.depth,3)}−${v(prevDepth,3)})/${v(dt,2)} = ${v(c.vDepth)} мм/год.`);else lines.push('По росту дефекта: не рассчитано — недостаточно согласованных исходных данных.');
    if(Number.isFinite(c.vCoupon))lines.push(`По потере массы: Vмасс = 8,76·10⁴·Δm/(A·τ·ρ) = 8,76·10⁴·${v(loss,3)}/(${v(area,3)}·${v(hours,3)}·${v(density,3)}) = ${v(c.vCoupon)} мм/год.`);else lines.push('По потере массы контрольного образца: не рассчитано — потеря массы не задана.');
    lines.push(`Непосредственно заданная скорость: ${v(direct)} мм/год.`,`Для консервативной оценки принята максимальная положительная скорость V = ${v(c.accepted)} мм/год.`,'','3. ОСТАТОЧНЫЙ РЕСУРС');
    lines.push(`По толщине стенки: Tδ = (δф−δдоп)/V = (${v(c.current,3)}−${v(c.allow,3)})/${v(c.accepted)} = ${v(c.lifeThk,2)} лет.`,`По глубине дефекта: Tc = (cдоп−cф)/V = (${v(c.depthAllow,3)}−${v(c.depth,3)})/${v(c.accepted)} = ${v(c.lifeDepth,2)} лет.`);
    if(Number.isFinite(c.lifePit))lines.push(`Язвенная коррозия: hкр = δ0−d·√[0,19P/(0,75σт)] = ${v(c.hCrit,3)} мм.`,`Скорость роста язвы Vк1 = ${v(c.vPit)} мм/год; остаточный ресурс Tпит = ${v(c.lifePit,2)} лет.`,`Для расчёта использованы d = ${v(pitSize,3)} мм; P = ${v(pitPressure,3)} МПа; σт = ${v(pitYield,3)} МПа.`);
    lines.push('', '4. ПРОГНОЗ НА ЗАДАННЫЙ ПЕРИОД',`Горизонт прогноза: ${v(c.horizon,2)} лет.`,`Прогнозная толщина: δпр = δф−V·T = ${v(c.current,3)}−${v(c.accepted)}·${v(c.horizon,2)} = ${v(c.forecastThk,3)} мм.`,`Прогнозная глубина дефекта: cпр = cф+V·T = ${v(c.depth,3)}+${v(c.accepted)}·${v(c.horizon,2)} = ${v(c.forecastDepth,3)} мм.`,'','5. ЗАКЛЮЧЕНИЕ',`Минимальный расчётный остаточный ресурс: ${v(c.lifeMin,2)} лет.`,`Прогнозный год достижения предельного состояния: ${v(c.limitYear,2)}.`);
    if(c.lifeMin<=0)lines.push('На дату обследования один из проверяемых пределов уже достигнут или превышен. Требуется инженерная оценка и решение эксплуатирующей организации.');else lines.push(`При неизменной принятой скорости коррозии расчётный предел не будет достигнут ранее чем через ${v(c.lifeMin,2)} лет.`);
    lines.push('','Примечание. Расчёт прямой трубы по внутреннему давлению не учитывает продольные напряжения, устойчивость, сейсмические воздействия, локальное взаимодействие дефектов, трещины, состояние сварных соединений и фасонных деталей. Исходные характеристики материала и коэффициенты подлежат подтверждению по проектной и исполнительной документации.','',`Дата формирования протокола: ${new Date().toLocaleString('ru-RU')}`);
    out.textContent=lines.join('\n');return out.textContent
  }
  async function copyCorrosionProtocol(){const text=buildCorrosionProtocol();if(!text)return;try{await navigator.clipboard.writeText(text);alert('Протокол скопирован в буфер обмена.')}catch{const ta=document.createElement('textarea');ta.value=text;document.body.appendChild(ta);ta.select();document.execCommand('copy');ta.remove();alert('Протокол скопирован в буфер обмена.')}}
  function downloadCorrosionProtocol(){const text=buildCorrosionProtocol();if(!text)return;const blob=new Blob([text],{type:'text/plain;charset=utf-8'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='protokol_korrozionnogo_prognoza.txt';a.click();URL.revokeObjectURL(a.href)}
  function printCorrosionProtocol(){buildCorrosionProtocol();document.body.classList.add('print-corrosion');const cleanup=()=>document.body.classList.remove('print-corrosion');window.addEventListener('afterprint',cleanup,{once:true});window.print();setTimeout(cleanup,1000)}
  function updateReport(){const lines=[`РАСЧЁТ ЭЛЕКТРИЧЕСКИХ СОПРОТИВЛЕНИЙ И КОРРОЗИОННЫЙ ПРОГНОЗ`,`Участок: ${$('name').value}`,`Покрытие: ${$('coating').selectedOptions[0]?.text||'—'}`,`Период: ${$('yearStart').value}–${$('yearSurvey').value}; D = ${$('diameter').value} мм; δ = ${$('thickness').value} мм`,``,`Нормативное сопротивление на дату обследования: ${sci(state.rNorm)} Ом·м²`,`Продольное сопротивление трубы: ${sci(state.rt)} Ом/м`];if(state.integral)lines.push('',`Интегральный метод по потенциальной диаграмме:`,`  Sл = ${fmt(state.integral.SL,3)} В·м; Sп = ${fmt(state.integral.SP,3)} В·м`,`  αл = ${sci(state.integral.aL)} 1/м; αп = ${sci(state.integral.aR)} 1/м`,`  R′п.л = ${sci(state.integral.rpl)} Ом·м²; R′п.п = ${sci(state.integral.rpr)} Ом·м²`,`  Среднее R′п = ${sci(state.integral.avg)} Ом·м²`,`  Сходимость: ${state.integral.k} итераций, ε = ${sci(state.integral.err)}`);if(state.measured)lines.push('',`Расчёт из измеренного Rп:`,`  Rп = ${sci(state.measured.rp)} Ом·м²; Rр = ${sci(state.measured.spread)} Ом·м²`,`  Rиз = ${sci(state.measured.rIso)} Ом·м²`);if(state.band)lines.push('',`Локальное измерение в шурфе:`,`  F = ${fmt(state.band.area,5)} м²; R = ${sci(state.band.r)} Ом·м²`);if(state.allowable)lines.push('',`Минимально допустимая толщина:`,`  Режим: ${state.allowable.method==='sp36'?'СП 36.13330 с минимумами по DN':'эксплуатационная оценка по давлению'}`,`  R₁ = ${fmt(state.allowable.R1,3)} МПа; δp = ${fmt(state.allowable.pressure,3)} мм`,`  Итоговая δдоп = ${fmt(state.allowable.final,3)} мм; запас = ${fmt(state.allowable.margin,3)} мм`);if(state.corrosion)lines.push('',`Коррозионный прогноз:`,`  Скорость за срок эксплуатации: ${sci(state.corrosion.vService)} мм/год`,`  Скорость между обследованиями: ${sci(state.corrosion.vBetween)} мм/год`,`  Скорость по росту дефекта: ${sci(state.corrosion.vDepth)} мм/год`,`  Скорость по потере массы: ${sci(state.corrosion.vCoupon)} мм/год`,`  Принятая консервативная скорость: ${sci(state.corrosion.accepted)} мм/год`,`  Ресурс по толщине: ${fmt(state.corrosion.lifeThk,2)} лет`,`  Ресурс по глубине: ${fmt(state.corrosion.lifeDepth,2)} лет`,`  Ресурс при питтинге: ${fmt(state.corrosion.lifePit,2)} лет`,`  Минимальный остаточный ресурс: ${fmt(state.corrosion.lifeMin,2)} лет`,`  Прогнозный год достижения предела: ${fmt(state.corrosion.limitYear,2)}`);lines.push('',`Предельно допустимое сопротивление покрытия: ${fmt(num('rLimit'),2)} Ом·м²`,`Дата формирования: ${new Date().toLocaleString('ru-RU')}`);$('report').textContent=lines.join('\n')}
  function formData(){const data={};document.querySelectorAll('input,select,textarea').forEach(el=>{if(el.id)data[el.id]=el.value});return data}
  function save(){localStorage.setItem('gasPipeCalc',JSON.stringify(formData()));alert('Исходные данные сохранены в браузере.')}
  function restore(){try{const data=JSON.parse(localStorage.getItem('gasPipeCalc')||'null');if(!data)return;for(const[k,v]of Object.entries(data))if($(k))$(k).value=v;updateCoatings();for(const[k,v]of Object.entries(data))if($(k))$(k).value=v}catch{}}
  function exportJson(){const payload={version:'1.0',savedAt:new Date().toISOString(),inputs:formData(),results:state};const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='raschet_soprotivleniy_gazoprovoda.json';a.click();URL.revokeObjectURL(a.href)}
  function all(){calcNorm();calcLong();calcIntegral();calcMeasured();calcBand();calcAllowThickness(true);calcCorrosion();updateReport()}
  function updateSteel(){let selected=$('steel').selectedOptions[0];if(!selected){const old=$('steelRho').value,match=[...$('steel').options].find(o=>o.dataset.rho===old);$('steel').value=match?match.value:'custom';selected=$('steel').selectedOptions[0]}const custom=$('steel').value==='custom';if(!custom&&selected?.dataset.rho)$('steelRho').value=selected.dataset.rho;$('steelRho').readOnly=!custom;const rho=Number($('steelRho').value);$('steelRhoMm').value=Number.isFinite(rho)?(rho*1e6).toLocaleString('ru-RU',{useGrouping:false,maximumFractionDigits:6}):'—'}
  $('steel').addEventListener('change',()=>{updateSteel();calcLong()});$('steelRho').addEventListener('input',updateSteel);$('normMethod').addEventListener('change',updateCoatings);$('coating').addEventListener('change',applyCoating);$('calcNorm').onclick=calcNorm;$('calcLong').onclick=calcLong;$('calcIntegral').onclick=calcIntegral;$('calcMeasured').onclick=calcMeasured;$('calcBand').onclick=calcBand;$('calcAllowThickness').onclick=()=>{if(calcAllowThickness(true))calcCorrosion()};$('calcCorrosion').onclick=calcCorrosion;$('makeCorrosionProtocol').onclick=buildCorrosionProtocol;$('copyCorrosionProtocol').onclick=copyCorrosionProtocol;$('downloadCorrosionProtocol').onclick=downloadCorrosionProtocol;$('printCorrosionProtocol').onclick=printCorrosionProtocol;$('allCalc').onclick=all;$('printBtn').onclick=()=>window.print();$('exportBtn').onclick=exportJson;$('saveBtn').onclick=save;$('resetBtn').onclick=()=>{if(confirm('Сбросить сохранённые и введённые данные?')){localStorage.removeItem('gasPipeCalc');location.reload()}};$('toggleTable').onclick=()=>{$('longTableCard').classList.toggle('hidden')};$('loadExample').onclick=()=>{ $('leftData').value='0; 1.80\n1000; 1.20\n2000; 0.78\n3000; 0.48\n4000; 0.25';$('rightData').value='0; 1.80\n1000; 1.28\n2000; 0.86\n3000; 0.55\n4000; 0.31';calcIntegral()};document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));t.classList.add('active');$(t.dataset.panel).classList.add('active')});
  updateCoatings();restore();updateSteel();calcNorm();calcLong();calcIntegral();calcMeasured();calcBand();calcAllowThickness(true);calcCorrosion();updateReport();
})();

// --- добавлено при переносе на сайт (tools/port_calculator.py) ---
// Отметка в журнал сайта: только факт расчёта, без введённых данных и результатов.
(() => {
  'use strict';
  const box = document.getElementById('siteLog');
  if (!box || !window.fetch) return;
  const token = box.querySelector('input[name=csrfmiddlewaretoken]');
  const send = kind => {
    const body = new URLSearchParams({ kind });
    fetch(box.dataset.url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': token ? token.value : '' },
      body,
    }).catch(() => {});
  };
  const kinds = {
    calcCorrosion: 'calc',
    calcAllowThickness: 'calc',
    allCalc: 'calc',
    downloadCorrosionProtocol: 'download',
  };
  for (const [id, kind] of Object.entries(kinds)) {
    const button = document.getElementById(id);
    if (button) button.addEventListener('click', () => send(kind));
  }
})();
