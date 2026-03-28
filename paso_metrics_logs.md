**PASO Experiments \& Results Analysis**



run\_id,scenario,station\_id,stage,start\_ts,end\_ts,duration\_ms,cpu\_percent,memory\_mb,temperature\_c,notes



**Official Baseline Happy-Path Timing**



**Test 1**

20260326T180015\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,mqtt\_transport,1774708685.712,1774519215.213521,0.0,7.8,224.266,57.85,"{""measured"": true, ""mqtt\_transport\_ms"": 0.0, ""source"": ""firmware\_dosing""}"

20260326T180015\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,event\_queueing,1774519215.214663,1774519215.236171,21.508,0.0,224.281,57.85,"{""event\_type"": ""removal"", ""firmware\_dosing\_active"": true, ""source"": ""firmware\_dosing""}"

20260326T180015\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,camera\_init,1774519215.249936,1774519222.558324,7308.389,6.7,229.469,56.75,"{""camera\_device"": 0, ""camera\_ready"": true, ""frame\_means"": \[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 125.4, 118.13, 112.3, 104.55, 97.23, 88.84, 85.66, 82.75, 83.18, 83.37], ""warmup\_frames"": 20}"

20260326T180015\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,tag\_or\_identity\_check,1774519222.559924,1774519222.960984,401.062,6.5,229.469,57.3,"{""integrated\_mode"": true, ""method"": ""tag"", ""reason"": null, ""success"": true}"

20260326T180015\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,camera\_release,1774519222.962996,1774519222.979667,16.671,0.0,227.156,57.3,"{""camera\_device"": 0, ""camera\_ready\_before\_release"": false}"

20260326T180015\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,identity\_check\_total,1774519215.249909,1774519222.981129,7731.219,0.0,227.156,57.3,"{""integrated\_mode"": true, ""method"": ""tag"", ""reason"": null, ""success"": true}"

20260326T180015\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,dosage\_check,1774519222.991443,1774519222.992481,1.037,25.0,227.156,58.4,"{""actual\_dosage"": 2, ""expected\_dosage"": 2, ""tolerance\_g"": 0.12, ""verified"": true, ""weight\_delta\_error\_g"": 0.0}"

20260326T180015\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_camera\_init,1774519223.030801,1774519226.208202,3177.397,0.0,349.406,58.95,"{""camera\_opened"": true, ""device\_id"": 0, ""fps"": 20}"

20260326T180015\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_frame\_loop,1774519226.209984,1774519256.253021,30043.036,34.6,359.391,74.35,"{""avg\_loop\_ms"": 63.104, ""hand\_motion\_count"": 14, ""monitoring\_duration\_s"": 30.044, ""peak\_loop\_ms"": 108.078, ""processed\_frame\_count"": 476, ""swallow\_count"": 2}"

20260326T180015\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_session,1774519222.994325,1774519256.26864,33274.315,32.0,295.078,74.9,"{""attempt"": 1, ""avg\_loop\_ms"": 63.104, ""compliance\_status"": ""good"", ""processed\_frame\_count"": 476, ""swallow\_count"": 2}"

20260326T180015\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,decision\_engine,1774519256.26977,1774519256.269808,0.038,0.0,295.078,74.9,"{""decision\_source"": ""decision\_engine\_verify\_final"", ""expected\_dosage"": 2, ""result"": ""success"", ""swallow\_count"": 2, ""verified"": true}"

20260326T180015\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,decision\_output,1774519256.270628,1774519256.274613,3.988,0.0,295.078,74.35,"{""result"": ""success"", ""verified"": true}"

20260326T180015\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,database\_logging,1774519256.278566,1774519256.292188,13.621,71.4,295.078,75.45,"{""logged"": true, ""result"": ""success""}"

20260326T180015\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,output\_logging,1774519256.270625,1774519256.293207,22.582,0.0,295.078,75.45,"{""database\_logged"": true, ""result"": ""success"", ""scheduler\_marked"": true, ""verified"": true}"

20260326T180015\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,pipeline\_total,1774519215.24025,1774519259.300912,44060.663,1.5,295.078,69.95,"{""event\_type"": ""removal"", ""final\_outcome"": ""success"", ""firmware\_dosing\_active"": true, ""source"": ""firmware\_dosing"", ""verified"": true}"



**Test 2**

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,mqtt\_transport,1774710853.962,1774519223.794852,0.0,1.5,224.547,55.65,"{""measured"": true, ""mqtt\_transport\_ms"": 0.0, ""source"": ""firmware\_dosing""}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,event\_queueing,1774519223.79604,1774519223.880716,84.676,50.0,224.547,55.65,"{""event\_type"": ""removal"", ""firmware\_dosing\_active"": true, ""source"": ""firmware\_dosing""}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,camera\_init,1774519223.893935,1774519231.177968,7284.034,0.5,229.656,57.3,"{""camera\_device"": 0, ""camera\_ready"": true, ""frame\_means"": \[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 160.26, 159.76, 152.28, 141.53, 125.38, 126.6, 138.31, 142.63, 149.8, 151.55], ""warmup\_frames"": 20}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,tag\_or\_identity\_check,1774519231.179497,1774519233.081904,1902.408,0.0,229.672,56.75,"{""integrated\_mode"": true, ""method"": ""tag"", ""reason"": null, ""success"": true}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,camera\_release,1774519233.083728,1774519233.088541,4.813,0.0,227.359,56.2,"{""camera\_device"": 0, ""camera\_ready\_before\_release"": false}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,identity\_check\_total,1774519223.893912,1774519233.089911,9195.998,0.0,227.359,56.2,"{""integrated\_mode"": true, ""method"": ""tag"", ""reason"": null, ""success"": true}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,dosage\_check,1774519233.099926,1774519233.100926,0.998,25.0,227.359,56.2,"{""actual\_dosage"": 2, ""expected\_dosage"": 2, ""tolerance\_g"": 0.12, ""verified"": true, ""weight\_delta\_error\_g"": 0.0}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_camera\_init,1774519233.132838,1774519236.345117,3212.272,0.0,345.609,57.85,"{""camera\_opened"": true, ""device\_id"": 0, ""fps"": 20}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_frame\_loop,1774519236.346958,1774519266.389353,30042.394,27.0,358.203,72.15,"{""avg\_loop\_ms"": 69.211, ""hand\_motion\_count"": 6, ""monitoring\_duration\_s"": 30.043, ""peak\_loop\_ms"": 135.554, ""processed\_frame\_count"": 434, ""swallow\_count"": 2}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_session,1774519233.102704,1774519266.44432,33341.616,24.7,296.125,72.7,"{""attempt"": 1, ""avg\_loop\_ms"": 69.211, ""compliance\_status"": ""good"", ""processed\_frame\_count"": 434, ""swallow\_count"": 2}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,decision\_engine,1774519266.445395,1774519266.445435,0.039,0.0,296.125,72.7,"{""decision\_source"": ""decision\_engine\_verify\_final"", ""expected\_dosage"": 2, ""result"": ""success"", ""swallow\_count"": 2, ""verified"": true}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,decision\_output,1774519266.446272,1774519266.450068,3.799,0.0,296.125,72.7,"{""result"": ""success"", ""verified"": true}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,database\_logging,1774519266.453041,1774519266.463183,10.142,50.0,296.125,72.7,"{""logged"": true, ""result"": ""success""}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,output\_logging,1774519266.446268,1774519266.464195,17.927,0.0,296.125,72.7,"{""database\_logged"": true, ""result"": ""success"", ""scheduler\_marked"": true, ""verified"": true}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,pipeline\_total,1774519223.884575,1774519269.476244,45591.67,0.2,296.125,67.75,"{""event\_type"": ""removal"", ""final\_outcome"": ""success"", ""firmware\_dosing\_active"": true, ""source"": ""firmware\_dosing"", ""verified"": true}"



**Test 3**

20260326T180014\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,mqtt\_transport,1774710958.596,1774519214.010099,0.0,2.0,224.422,61.15,"{""measured"": true, ""mqtt\_transport\_ms"": 0.0, ""source"": ""firmware\_dosing""}"

20260326T180014\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,event\_queueing,1774519214.01143,1774519214.037,25.571,50.0,224.438,61.15,"{""event\_type"": ""removal"", ""firmware\_dosing\_active"": true, ""source"": ""firmware\_dosing""}"

20260326T180014\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,camera\_init,1774519214.050556,1774519221.341967,7291.412,0.6,229.625,60.05,"{""camera\_device"": 0, ""camera\_ready"": true, ""frame\_means"": \[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 133.36, 133.37, 137.02, 136.61, 136.53, 136.42, 135.92, 135.65, 135.77, 135.76], ""warmup\_frames"": 20}"

20260326T180014\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,tag\_or\_identity\_check,1774519221.343464,1774519223.445542,2102.078,0.1,229.625,60.05,"{""integrated\_mode"": true, ""method"": ""tag"", ""reason"": null, ""success"": true}"

20260326T180014\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,camera\_release,1774519223.447377,1774519223.462521,15.144,14.3,227.312,61.15,"{""camera\_device"": 0, ""camera\_ready\_before\_release"": false}"

20260326T180014\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,identity\_check\_total,1774519214.050535,1774519223.463844,9413.308,0.0,227.312,60.05,"{""integrated\_mode"": true, ""method"": ""tag"", ""reason"": null, ""success"": true}"

20260326T180014\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,dosage\_check,1774519223.474004,1774519223.475636,1.63,0.0,227.312,60.05,"{""actual\_dosage"": 2, ""expected\_dosage"": 2, ""tolerance\_g"": 0.12, ""verified"": true, ""weight\_delta\_error\_g"": 0.0}"

20260326T180014\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_camera\_init,1774519223.505147,1774519226.703437,3198.288,0.0,345.625,61.7,"{""camera\_opened"": true, ""device\_id"": 0, ""fps"": 20}"

20260326T180014\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_frame\_loop,1774519226.705436,1774519256.707688,30002.251,26.1,356.219,76.0,"{""avg\_loop\_ms"": 72.634, ""hand\_motion\_count"": 19, ""monitoring\_duration\_s"": 30.003, ""peak\_loop\_ms"": 149.189, ""processed\_frame\_count"": 413, ""swallow\_count"": 2}"

20260326T180014\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_session,1774519223.47732,1774519256.747984,33270.664,24.6,291.906,75.45,"{""attempt"": 1, ""avg\_loop\_ms"": 72.634, ""compliance\_status"": ""good"", ""processed\_frame\_count"": 413, ""swallow\_count"": 2}"

20260326T180014\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,decision\_engine,1774519256.749068,1774519256.749107,0.038,0.0,291.906,75.45,"{""decision\_source"": ""decision\_engine\_verify\_final"", ""expected\_dosage"": 2, ""result"": ""success"", ""swallow\_count"": 2, ""verified"": true}"

20260326T180014\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,decision\_output,1774519256.750018,1774519256.753913,3.899,33.3,291.906,74.9,"{""result"": ""success"", ""verified"": true}"

20260326T180014\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,database\_logging,1774519256.75771,1774519256.772002,14.292,42.9,291.906,76.0,"{""logged"": true, ""result"": ""success""}"

20260326T180014\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,output\_logging,1774519256.750014,1774519256.773068,23.054,0.0,291.906,76.0,"{""database\_logged"": true, ""result"": ""success"", ""scheduler\_marked"": true, ""verified"": true}"

20260326T180014\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,pipeline\_total,1774519214.040951,1774519259.785571,45744.621,0.2,291.906,69.95,"{""event\_type"": ""removal"", ""final\_outcome"": ""success"", ""firmware\_dosing\_active"": true, ""source"": ""firmware\_dosing"", ""verified"": true}"



**Test 4**

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,mqtt\_transport,1774711118.565,1774519223.758344,0.0,1.4,224.375,60.6,"{""measured"": true, ""mqtt\_transport\_ms"": 0.0, ""source"": ""firmware\_dosing""}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,event\_queueing,1774519223.759559,1774519223.828274,68.715,0.0,224.391,60.6,"{""event\_type"": ""removal"", ""firmware\_dosing\_active"": true, ""source"": ""firmware\_dosing""}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,camera\_init,1774519223.944529,1774519231.138117,7193.589,0.7,229.578,61.7,"{""camera\_device"": 0, ""camera\_ready"": true, ""frame\_means"": \[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 143.2, 145.74, 145.35, 152.5, 154.61, 154.7, 151.97, 146.61, 127.67], ""warmup\_frames"": 20}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,tag\_or\_identity\_check,1774519231.13965,1774519231.841531,701.883,0.0,229.594,61.7,"{""integrated\_mode"": true, ""method"": ""tag"", ""reason"": null, ""success"": true}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,camera\_release,1774519231.843411,1774519231.851019,7.608,0.0,227.281,61.7,"{""camera\_device"": 0, ""camera\_ready\_before\_release"": false}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,identity\_check\_total,1774519223.944481,1774519231.852409,7907.927,100.0,227.281,61.7,"{""integrated\_mode"": true, ""method"": ""tag"", ""reason"": null, ""success"": true}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,dosage\_check,1774519231.863459,1774519231.865596,2.137,20.0,227.281,61.7,"{""actual\_dosage"": 2, ""expected\_dosage"": 2, ""tolerance\_g"": 0.12, ""verified"": true, ""weight\_delta\_error\_g"": 0.0}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_camera\_init,1774519231.896098,1774519235.091427,3195.325,0.0,349.484,62.8,"{""camera\_opened"": true, ""device\_id"": 0, ""fps"": 20}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_frame\_loop,1774519235.092943,1774519265.15771,30064.765,26.7,358.922,76.55,"{""avg\_loop\_ms"": 67.856, ""hand\_motion\_count"": 17, ""monitoring\_duration\_s"": 30.065, ""peak\_loop\_ms"": 130.797, ""processed\_frame\_count"": 443, ""swallow\_count"": 2}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_session,1774519231.867335,1774519265.206893,33339.557,24.4,294.609,75.45,"{""attempt"": 1, ""avg\_loop\_ms"": 67.856, ""compliance\_status"": ""good"", ""processed\_frame\_count"": 443, ""swallow\_count"": 2}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,decision\_engine,1774519265.207976,1774519265.208014,0.038,0.0,294.609,75.45,"{""decision\_source"": ""decision\_engine\_verify\_final"", ""expected\_dosage"": 2, ""result"": ""success"", ""swallow\_count"": 2, ""verified"": true}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,decision\_output,1774519265.208856,1774519265.220486,11.633,16.7,294.609,75.45,"{""result"": ""success"", ""verified"": true}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,database\_logging,1774519265.22368,1774519265.237233,13.552,66.7,294.609,76.0,"{""logged"": true, ""result"": ""success""}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,output\_logging,1774519265.208852,1774519265.238265,29.413,0.0,294.609,76.0,"{""database\_logged"": true, ""result"": ""success"", ""scheduler\_marked"": true, ""verified"": true}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,pipeline\_total,1774519223.832415,1774519268.250384,44417.97,0.1,294.609,71.05,"{""event\_type"": ""removal"", ""final\_outcome"": ""success"", ""firmware\_dosing\_active"": true, ""source"": ""firmware\_dosing"", ""verified"": true}"



**Test 5**

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,mqtt\_transport,1774711281.318,1774519223.260769,0.0,1.4,224.453,60.6,"{""measured"": true, ""mqtt\_transport\_ms"": 0.0, ""source"": ""firmware\_dosing""}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,event\_queueing,1774519223.262581,1774519223.353947,91.366,0.0,224.469,60.6,"{""event\_type"": ""removal"", ""firmware\_dosing\_active"": true, ""source"": ""firmware\_dosing""}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,camera\_init,1774519223.367667,1774519230.67104,7303.373,0.3,229.656,61.7,"{""camera\_device"": 0, ""camera\_ready"": true, ""frame\_means"": \[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 145.77, 153.87, 159.36, 159.8, 155.84, 152.39, 149.35, 148.96, 149.24, 148.7], ""warmup\_frames"": 20}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,tag\_or\_identity\_check,1774519230.67259,1774519231.874067,1201.477,0.0,229.656,61.15,"{""integrated\_mode"": true, ""method"": ""tag"", ""reason"": null, ""success"": true}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,camera\_release,1774519231.875934,1774519231.892944,17.009,12.5,227.344,61.15,"{""camera\_device"": 0, ""camera\_ready\_before\_release"": false}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,identity\_check\_total,1774519223.367641,1774519231.894304,8526.662,0.0,227.344,61.15,"{""integrated\_mode"": true, ""method"": ""tag"", ""reason"": null, ""success"": true}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,dosage\_check,1774519231.904538,1774519231.90551,0.971,20.0,227.344,61.15,"{""actual\_dosage"": 2, ""expected\_dosage"": 2, ""tolerance\_g"": 0.12, ""verified"": true, ""weight\_delta\_error\_g"": 0.0}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_camera\_init,1774519231.937865,1774519235.149576,3211.709,0.0,340.688,63.35,"{""camera\_opened"": true, ""device\_id"": 0, ""fps"": 20}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_frame\_loop,1774519235.151125,1774519265.200748,30049.622,26.2,352.969,78.2,"{""avg\_loop\_ms"": 66.324, ""hand\_motion\_count"": 15, ""monitoring\_duration\_s"": 30.05, ""peak\_loop\_ms"": 119.817, ""processed\_frame\_count"": 453, ""swallow\_count"": 2}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_session,1774519231.907285,1774519265.247922,33340.638,24.0,288.656,76.55,"{""attempt"": 1, ""avg\_loop\_ms"": 66.324, ""compliance\_status"": ""good"", ""processed\_frame\_count"": 453, ""swallow\_count"": 2}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,decision\_engine,1774519265.249075,1774519265.249117,0.041,0.0,288.656,76.55,"{""decision\_source"": ""decision\_engine\_verify\_final"", ""expected\_dosage"": 2, ""result"": ""success"", ""swallow\_count"": 2, ""verified"": true}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,decision\_output,1774519265.249948,1774519265.254253,4.307,0.0,288.656,75.45,"{""result"": ""success"", ""verified"": true}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,database\_logging,1774519265.258213,1774519265.270521,12.307,60.0,288.656,76.0,"{""logged"": true, ""result"": ""success""}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,output\_logging,1774519265.249944,1774519265.271595,21.651,100.0,288.656,76.55,"{""database\_logged"": true, ""result"": ""success"", ""scheduler\_marked"": true, ""verified"": true}"

20260326T180023\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,pipeline\_total,1774519223.357957,1774519268.284549,44926.592,0.7,288.656,71.6,"{""event\_type"": ""removal"", ""final\_outcome"": ""success"", ""firmware\_dosing\_active"": true, ""source"": ""firmware\_dosing"", ""verified"": true}"



**Test Underdose**

20260326T180020\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,mqtt\_transport,1774714145.895,1774519220.528675,0.0,1.5,224.391,55.65,"{""measured"": true, ""mqtt\_transport\_ms"": 0.0, ""source"": ""firmware\_dosing""}"

20260326T180020\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,event\_queueing,1774519220.529956,1774519220.563376,33.42,0.0,224.391,55.65,"{""event\_type"": ""removal"", ""firmware\_dosing\_active"": true, ""source"": ""firmware\_dosing""}"

20260326T180020\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,camera\_init,1774519220.576891,1774519227.836447,7259.557,0.4,229.578,55.65,"{""camera\_device"": 0, ""camera\_ready"": true, ""frame\_means"": \[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 140.4, 135.29, 137.44, 137.21, 137.41, 137.67, 137.75, 137.77, 137.73, 137.82], ""warmup\_frames"": 20}"

20260326T180020\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,tag\_or\_identity\_check,1774519227.837985,1774519230.041036,2203.051,0.0,229.578,56.2,"{""integrated\_mode"": true, ""method"": ""tag"", ""reason"": null, ""success"": true}"

20260326T180020\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,camera\_release,1774519230.043029,1774519230.050369,7.34,0.0,227.266,55.65,"{""camera\_device"": 0, ""camera\_ready\_before\_release"": false}"

20260326T180020\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,identity\_check\_total,1774519220.576869,1774519230.051742,9474.872,0.0,227.266,55.65,"{""integrated\_mode"": true, ""method"": ""tag"", ""reason"": null, ""success"": true}"

20260326T180020\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,dosage\_check,1774519230.063418,1774519230.064608,1.189,42.9,227.266,55.65,"{""actual\_dosage"": 2, ""expected\_dosage"": 2, ""tolerance\_g"": 0.12, ""verified"": true, ""weight\_delta\_error\_g"": 0.0}"

20260326T180020\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_camera\_init,1774519230.097239,1774519233.274955,3177.712,0.0,345.594,57.85,"{""camera\_opened"": true, ""device\_id"": 0, ""fps"": 20}"

20260326T180020\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_frame\_loop,1774519233.276534,1774519263.311153,30034.618,30.3,356.031,72.15,"{""avg\_loop\_ms"": 51.862, ""hand\_motion\_count"": 1, ""monitoring\_duration\_s"": 30.035, ""peak\_loop\_ms"": 135.437, ""processed\_frame\_count"": 579, ""swallow\_count"": 0}"

20260326T180020\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_session,1774519230.066367,1774519263.337984,33271.617,27.7,291.719,72.7,"{""attempt"": 1, ""avg\_loop\_ms"": 51.862, ""compliance\_status"": ""acceptable"", ""processed\_frame\_count"": 579, ""swallow\_count"": 0}"

20260326T180020\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,decision\_output,1774519263.339266,1774519263.342396,3.131,0.0,291.719,72.7,"{""expected\_dosage"": 2, ""feedback"": ""incomplete\_intake\_retry\_prompt"", ""swallow\_count"": 0}"

20260326T180020\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_camera\_init,1774519266.380127,1774519270.304419,3924.289,0.0,395.312,68.3,"{""camera\_opened"": true, ""device\_id"": 0, ""fps"": 20}"

20260326T180020\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_frame\_loop,1774519270.305854,1774519300.361783,30055.928,29.7,398.781,78.75,"{""avg\_loop\_ms"": 60.463, ""hand\_motion\_count"": 0, ""monitoring\_duration\_s"": 30.056, ""peak\_loop\_ms"": 102.505, ""processed\_frame\_count"": 497, ""swallow\_count"": 0}"

20260326T180020\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_session,1774519266.345285,1774519300.398833,34053.548,24.9,396.234,76.55,"{""attempt"": 2, ""avg\_loop\_ms"": 60.463, ""compliance\_status"": ""no\_intake"", ""processed\_frame\_count"": 497, ""swallow\_count"": 0}"

20260326T180020\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,decision\_engine,1774519300.400303,1774519300.400313,0.01,0.0,396.234,76.55,"{""actual\_dosage"": 0, ""decision\_source"": ""manual\_incorrect\_dosage\_incomplete\_intake\_under"", ""expected\_dosage"": 2, ""result"": ""incorrect\_dosage"", ""verified"": false}"

20260326T180020\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,decision\_output,1774519300.401199,1774519302.376216,1975.017,0.8,396.25,74.35,"{""result"": ""incorrect\_dosage"", ""verified"": false}"

20260326T180020\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,database\_logging,1774519302.377729,1774519302.388999,11.27,20.0,396.25,73.8,"{""logged"": true, ""result"": ""incorrect\_dosage""}"

20260326T180020\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,output\_logging,1774519300.401196,1774519302.390482,1989.288,0.0,396.25,73.8,"{""database\_logged"": true, ""result"": ""incorrect\_dosage"", ""scheduler\_marked"": false, ""verified"": false}"

20260326T180020\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,pipeline\_total,1774519220.567468,1774519307.405076,86837.609,0.3,396.25,69.4,"{""event\_type"": ""removal"", ""final\_outcome"": ""incorrect\_dosage"", ""firmware\_dosing\_active"": true, ""source"": ""firmware\_dosing"", ""verified"": false}"



**Test Overdose**

20260326T180016\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,mqtt\_transport,1774715083.851,1774519216.85108,0.0,1.8,224.297,57.85,"{""measured"": true, ""mqtt\_transport\_ms"": 0.0, ""source"": ""firmware\_dosing""}"

20260326T180016\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,event\_queueing,1774519216.852379,1774519216.860775,8.397,0.0,224.312,57.85,"{""event\_type"": ""removal"", ""firmware\_dosing\_active"": true, ""source"": ""firmware\_dosing""}"

20260326T180016\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,camera\_init,1774519216.874748,1774519224.248869,7374.121,0.6,229.5,57.85,"{""camera\_device"": 0, ""camera\_ready"": true, ""frame\_means"": \[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 133.75, 133.9, 136.24, 136.5, 137.45, 137.94, 138.72, 128.64, 125.99, 122.76], ""warmup\_frames"": 20}"

20260326T180016\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,tag\_or\_identity\_check,1774519224.25032,1774519224.651218,400.898,0.0,229.5,57.85,"{""integrated\_mode"": true, ""method"": ""tag"", ""reason"": null, ""success"": true}"

20260326T180016\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,camera\_release,1774519224.653066,1774519224.669814,16.748,14.3,227.188,57.85,"{""camera\_device"": 0, ""camera\_ready\_before\_release"": false}"

20260326T180016\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,identity\_check\_total,1774519216.874724,1774519224.671154,7796.428,0.0,227.188,57.85,"{""integrated\_mode"": true, ""method"": ""tag"", ""reason"": null, ""success"": true}"

20260326T180016\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,dosage\_check,1774519224.681418,1774519224.682411,0.991,20.0,227.188,57.85,"{""actual\_dosage"": 2, ""expected\_dosage"": 2, ""tolerance\_g"": 0.12, ""verified"": true, ""weight\_delta\_error\_g"": 0.0}"

20260326T180016\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_camera\_init,1774519224.713202,1774519227.878119,3164.912,0.0,349.344,61.15,"{""camera\_opened"": true, ""device\_id"": 0, ""fps"": 20}"

20260326T180016\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_frame\_loop,1774519227.879657,1774519257.925669,30046.012,24.9,358.422,72.7,"{""avg\_loop\_ms"": 66.17, ""hand\_motion\_count"": 12, ""monitoring\_duration\_s"": 30.046, ""peak\_loop\_ms"": 120.928, ""processed\_frame\_count"": 454, ""swallow\_count"": 4}"

20260326T180016\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,monitoring\_session,1774519224.684065,1774519257.993506,33309.441,22.8,294.109,72.15,"{""attempt"": 1, ""avg\_loop\_ms"": 66.17, ""compliance\_status"": ""good"", ""processed\_frame\_count"": 454, ""swallow\_count"": 4}"

20260326T180016\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,decision\_engine,1774519257.994866,1774519257.994878,0.012,0.0,294.109,72.15,"{""actual\_dosage"": 4, ""decision\_source"": ""manual\_incorrect\_dosage\_intake\_monitoring"", ""expected\_dosage"": 2, ""result"": ""incorrect\_dosage"", ""verified"": false}"

20260326T180016\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,decision\_output,1774519257.995745,1774519259.849427,1853.682,0.8,294.109,69.95,"{""result"": ""incorrect\_dosage"", ""verified"": false}"

20260326T180016\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,database\_logging,1774519259.850468,1774519259.861077,10.608,0.0,294.109,68.85,"{""logged"": true, ""result"": ""incorrect\_dosage""}"

20260326T180016\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,output\_logging,1774519257.995741,1774519259.862056,1866.316,0.0,294.109,68.85,"{""database\_logged"": true, ""result"": ""incorrect\_dosage"", ""scheduler\_marked"": false, ""verified"": false}"

20260326T180016\_station\_2\_0001,paracetamol\_firmware\_dosing,station\_2,pipeline\_total,1774519216.865238,1774519264.876125,48010.887,0.4,294.109,66.1,"{""event\_type"": ""removal"", ""final\_outcome"": ""incorrect\_dosage"", ""firmware\_dosing\_active"": true, ""source"": ""firmware\_dosing"", ""verified"": false}"

