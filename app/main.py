"""HROne Employee Attendance & Analytics API."""
import os, re, calendar
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from contextlib import asynccontextmanager
from typing import Optional, Literal
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
from bson import ObjectId

load_dotenv()
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), serverSelectionTimeoutMS=3000)
db = client[os.getenv("MONGO_DB", "attendance_db")]
UTC=timezone.utc; IST=timezone(timedelta(hours=5, minutes=30))
PRESENCE={"PRESENT","WFH","ON_DUTY"}; STATUSES={"PRESENT","ABSENT","LEAVE","WFH","ON_DUTY"}

def indexes():
    db.employees.create_index("emp_code", unique=True, name="uq_employee_code")
    db.employees.create_index([("department",1),("joined_on",1)], name="department_joined")
    db.attendance_logs.create_index([("emp_code",1),("date",1)], unique=True, name="uq_attendance_employee_date")
    db.attendance_logs.create_index([("date",-1),("emp_code",1)], name="attendance_date_employee")
    db.attendance_logs.create_index([("emp_code",1),("date",1),("status",1)], name="attendance_employee_date_status")
    db.attendance_logs.create_index([("date",1),("late_minutes",-1),("emp_code",1)], name="attendance_month_late")

@asynccontextmanager
async def lifespan(app):
    indexes()
    yield
app=FastAPI(title="Employee Attendance & Analytics API",version="2.0.0",lifespan=lifespan)

# Helpers
def dt_ms(v):
    if v is None: return None
    if v.tzinfo is None: v=v.replace(tzinfo=UTC)
    return int(v.timestamp()*1000)
def api_doc(d):
    if not d: return d
    d=dict(d); d.pop("_id",None)
    for k in ("created_at","punch_in","punch_out"):
        if k in d: d[k]=dt_ms(d[k])
    d.setdefault("history",[]); d.setdefault("half_day",False); d.setdefault("late_minutes",0); d.setdefault("overtime_minutes",0)
    for h in d.get("history",[]):
        h["at"]=dt_ms(h.get("at"))
        for v in h.get("changes",{}).values():
            for side in ("from","to"):
                if isinstance(v.get(side),datetime): v[side]=dt_ms(v[side])
    return d
def valid_date(s):
    try: return date.fromisoformat(s)
    except Exception: raise HTTPException(422,"date must be YYYY-MM-DD")
def valid_month(s):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])",s): raise HTTPException(422,"month must be YYYY-MM")
    y,m=map(int,s.split('-')); return date(y,m,1),date(y,m,calendar.monthrange(y,m)[1])
def round_half(v, places=2):
    if v is None:return None
    q=Decimal('1').scaleb(-places)
    return float(Decimal(str(v)).quantize(q,rounding=ROUND_HALF_UP))
def parse_ms(v, default_now=False):
    if v is None:
        if default_now:return datetime.now(UTC).replace(microsecond=0)
        return None
    if isinstance(v,bool) or not isinstance(v,int) or v<100000000000 or v>4102444800000: raise HTTPException(422,"timestamp must be epoch milliseconds")
    try: return datetime.fromtimestamp(v/1000,UTC).replace(microsecond=0)
    except Exception: raise HTTPException(422,"timestamp out of range")
def as_utc(d):
    if d is None:return None
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)
def attendance_date(punch, emp):
    local=as_utc(punch).astimezone(IST); start=emp['shift_start']; end=emp['shift_end']
    if end<=start and local.strftime('%H:%M') < end:
        return (local.date()-timedelta(days=1)).isoformat()
    return local.date().isoformat()
def shift_end_dt(day, emp):
    h,m=map(int,emp['shift_end'].split(':')); d=date.fromisoformat(day)
    if emp['shift_end']<=emp['shift_start']: d+=timedelta(days=1)
    return datetime.combine(d,time(h,m),IST).astimezone(UTC)
def shift_start_dt(day, emp):
    h,m=map(int,emp['shift_start'].split(':')); d=date.fromisoformat(day)
    return datetime.combine(d,time(h,m),IST).astimezone(UTC)
def calc_derived(doc,emp):
    pi=doc.get('punch_in'); po=doc.get('punch_out'); status=doc.get('status')
    if status not in PRESENCE:
        return {'work_hours':None,'late_minutes':0,'overtime_minutes':0,'half_day':False}
    late=0
    if pi:
        delta=(as_utc(pi)-shift_start_dt(doc['date'],emp)).total_seconds()
        if delta>600: late=max(0,int(delta//60))
    if not po or not pi: return {'work_hours':None,'late_minutes':late,'overtime_minutes':0,'half_day':False}
    hours=round_half((as_utc(po)-as_utc(pi)).total_seconds()/3600,2)
    over=int((as_utc(po)-shift_end_dt(doc['date'],emp)).total_seconds()//60)
    if over<30: over=0
    return {'work_hours':hours,'late_minutes':late,'overtime_minutes':over,'half_day':hours<4.5}
def clean_employee(d):
    d=dict(d); d.pop('_id',None)
    if 'created_at' in d:d['created_at']=dt_ms(d['created_at'])
    return d

def count_workdays(start,end):
    if start>end:return 0
    n=0; cur=start
    while cur<=end:
        if cur.weekday()<5:n+=1
        cur+=timedelta(days=1)
    return n
class EmployeeIn(BaseModel):
    emp_code:str=Field(pattern=r'^EMP\d{4,6}$'); name:str=Field(min_length=1,max_length=100); email:str=Field(max_length=120,pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$'); department:str=Field(min_length=1,max_length=50)
    shift_start:str=Field(default='09:30',pattern=r'^([01]\d|2[0-3]):[0-5]\d$'); shift_end:str=Field(default='18:30',pattern=r'^([01]\d|2[0-3]):[0-5]\d$'); joined_on:str
    @field_validator('joined_on')
    @classmethod
    def check_joined(cls,v): valid_date(v); return v
    @model_validator(mode='after')
    def shifts_differ(self):
        if self.shift_start==self.shift_end: raise ValueError('shift_start must differ from shift_end')
        return self
class PunchInIn(BaseModel):
    emp_code:str; punched_at:Optional[int]=None; status:Literal['PRESENT','WFH','ON_DUTY']='PRESENT'
    @field_validator('punched_at')
    @classmethod
    def check_ts(cls,v):
        if v is not None:parse_ms(v)
        return v
class PunchOutIn(BaseModel):
    emp_code:str; punched_at:Optional[int]=None
    @field_validator('punched_at')
    @classmethod
    def check_ts(cls,v):
        if v is not None:parse_ms(v)
        return v
class RegularizeIn(BaseModel):
    status:Optional[Literal['PRESENT','ABSENT','LEAVE','WFH','ON_DUTY']]=None; punch_in:Optional[int]=None; punch_out:Optional[int]=None
    reason:str=Field(min_length=5,max_length=200); regularized_by:str=Field(min_length=1,max_length=50)
    @field_validator('punch_in','punch_out')
    @classmethod
    def check_ts(cls,v):
        if v is not None:parse_ms(v)
        return v

def pagination(page,page_size):
    if page<1 or not 1<=page_size<=100: raise HTTPException(422,'invalid pagination')

def month_range(month):
    a,b=valid_month(month); return a.isoformat(),b.isoformat()

@app.get('/health')
def health():
    try: client.admin.command('ping'); return {'status':'ok'}
    except Exception: raise HTTPException(503,'MongoDB unavailable')

@app.post('/employees',status_code=201)
def create_employee(body:EmployeeIn):
    doc=body.model_dump(); doc['created_at']=datetime.now(UTC).replace(microsecond=0)
    try: db.employees.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409,'emp_code already exists')
    return clean_employee(doc)
@app.get('/employees')
def list_employees(department:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    q={'department':department} if department is not None else {}; total=db.employees.count_documents(q)
    items=[clean_employee(x) for x in db.employees.find(q,{'_id':0}).sort('emp_code',1).skip((page-1)*page_size).limit(page_size)]
    return {'items':items,'total':total,'page':page,'page_size':page_size}

@app.post('/attendance/punch-in',status_code=201)
def punch_in(body:PunchInIn):
    emp=db.employees.find_one({'emp_code':body.emp_code})
    if not emp: raise HTTPException(404,'employee not found')
    ts=parse_ms(body.punched_at,True); d=attendance_date(ts,emp)
    doc={'emp_code':body.emp_code,'date':d,'status':body.status,'punch_in':ts,'punch_out':None,'work_hours':None,'late_minutes':0,'overtime_minutes':0,'half_day':False,'history':[]}
    doc.update(calc_derived(doc,emp))
    try: db.attendance_logs.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409,'already punched in for this date')
    return api_doc(doc)

@app.post('/attendance/punch-out')
def punch_out(body:PunchOutIn):
    emp=db.employees.find_one({'emp_code':body.emp_code})
    if not emp: raise HTTPException(404,'employee not found')
    ts=parse_ms(body.punched_at,True)
    rec=db.attendance_logs.find_one({'emp_code':body.emp_code,'punch_in':{'$lte':ts}},sort=[('punch_in',-1)])
    if not rec: raise HTTPException(404,'no punch-in found')
    pi=as_utc(rec.get('punch_in'))
    if ts<=pi or ts-pi>timedelta(hours=24): raise HTTPException(422,'punch-out must be after punch-in and within 24 hours')
    updated=dict(rec); updated['punch_out']=ts; updated.update(calc_derived(updated,emp))
    result=db.attendance_logs.find_one_and_update({'_id':rec['_id'],'punch_out':None},{'$set':{k:updated[k] for k in ('punch_out','work_hours','late_minutes','overtime_minutes','half_day')}},return_document=ReturnDocument.AFTER)
    if result is None:
        if db.attendance_logs.find_one({'_id':rec['_id']}): raise HTTPException(409,'already punched out')
        raise HTTPException(404,'record not found')
    return api_doc(result)

@app.get('/attendance')
def list_attendance(emp_code:Optional[str]=None,date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    q={}
    if emp_code:q['emp_code']=emp_code
    if date_from:
        valid_date(date_from); q.setdefault('date',{})['$gte']=date_from
    if date_to:
        valid_date(date_to); q.setdefault('date',{})['$lte']=date_to
    if date_from and date_to and date_from>date_to: raise HTTPException(422,'date_from must be <= date_to')
    if status:
        if status not in STATUSES: raise HTTPException(422,'invalid status')
        q['status']=status
    total=db.attendance_logs.count_documents(q)
    items=[api_doc(x) for x in db.attendance_logs.find(q,{'_id':0}).sort([('date',-1),('emp_code',1)]).skip((page-1)*page_size).limit(page_size)]
    return {'items':items,'total':total,'page':page,'page_size':page_size}

@app.patch('/attendance/{emp_code}/{day}')
def regularize(emp_code:str,day:str,body:RegularizeIn):
    valid_date(day); emp=db.employees.find_one({'emp_code':emp_code})
    if not emp: raise HTTPException(404,'employee not found')
    old=db.attendance_logs.find_one({'emp_code':emp_code,'date':day})
    if not old: raise HTTPException(404,'attendance record not found')
    new=dict(old); changes_in=body.model_dump(exclude={'reason','regularized_by'},exclude_unset=True)
    if body.status in ('ABSENT','LEAVE') and ('punch_in' in changes_in or 'punch_out' in changes_in): raise HTTPException(422,'ABSENT/LEAVE cannot include punch times')
    for k,v in changes_in.items(): new[k]=parse_ms(v) if k in ('punch_in','punch_out') and v is not None else v
    if new['status'] in ('ABSENT','LEAVE'): new['punch_in']=None; new['punch_out']=None
    elif new.get('punch_in') is None: raise HTTPException(422,'presence status requires punch_in')
    if new.get('punch_in') and attendance_date(new['punch_in'],emp)!=day: raise HTTPException(422,'punch_in must belong to attendance date')
    if new.get('punch_out'):
        if not new.get('punch_in') or as_utc(new['punch_out'])<=as_utc(new['punch_in']) or as_utc(new['punch_out'])-as_utc(new['punch_in'])>timedelta(hours=24): raise HTTPException(422,'invalid punch_out')
    new.update(calc_derived(new,emp))
    fields=['status','punch_in','punch_out','work_hours','late_minutes','overtime_minutes','half_day']; changes={}
    for k in fields:
        a=old.get(k); b=new.get(k)
        if isinstance(a,datetime): a=as_utc(a)
        if isinstance(b,datetime): b=as_utc(b)
        if a!=b: changes[k]={'from':old.get(k),'to':new.get(k)}
    if not changes: raise HTTPException(422,'request changes nothing')
    hist={'at':datetime.now(UTC).replace(microsecond=0),'by':body.regularized_by,'reason':body.reason,'changes':changes}
    # Optimistic concurrency: update only if the original values still match, append atomically.
    filt={'_id':old['_id'],'history':old.get('history',[]),'status':old.get('status')}
    for k in ('punch_in','punch_out','work_hours','late_minutes','overtime_minutes','half_day'):
        filt[k]=old.get(k)
    update={'$set':{k:new.get(k) for k in fields},'$push':{'history':hist}}
    result=db.attendance_logs.find_one_and_update(filt,update,return_document=ReturnDocument.AFTER)
    if result is None: raise HTTPException(409,'record changed concurrently; retry')
    return api_doc(result)

# Analytics are performed in MongoDB aggregation pipelines. Decimal128 arithmetic is avoided in output via rounding helpers.
def monthly_log_match(emp_code,month):
    start,end=month_range(month); return {'emp_code':emp_code,'date':{'$gte':start,'$lte':end}}
def present_expr(): return {'$cond':[{'$in':['$status',list(PRESENCE)]},{'$cond':[{'$eq':['$half_day',True]},0.5,1]},0]}
def working_day_expr(): return {'$in':[{'$dayOfWeek':{'$dateFromString':{'dateString':'$date'}}},[2,3,4,5,6]]}
@app.get('/analytics/employees/{emp_code}/monthly')
def employee_monthly(emp_code:str,month:str=Query(...,pattern=r'^\d{4}-(0[1-9]|1[0-2])$')):
    emp=db.employees.find_one({'emp_code':emp_code},{'_id':0,'joined_on':1})
    if not emp: raise HTTPException(404,'employee not found')
    start,end=month_range(month); first=max(date.fromisoformat(start),date.fromisoformat(emp['joined_on'])); last=date.fromisoformat(end)
    working=count_workdays(first,last)
    pipe=[{'$match':monthly_log_match(emp_code,month)},{'$group':{'_id':None,'present_days':{'$sum':{'$cond':[{'$and':[{'$in':['$status',list(PRESENCE)]},working_day_expr()]},{'$cond':[{'$eq':['$half_day',True]},0.5,1]},0]}},'leave_days':{'$sum':{'$cond':[{'$eq':['$status','LEAVE']},1,0]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$late_minutes',0]},0]},1,0]}},'total_late_minutes':{'$sum':{'$ifNull':['$late_minutes',0]}},'total_overtime_minutes':{'$sum':{'$ifNull':['$overtime_minutes',0]}}}}]
    r=list(db.attendance_logs.aggregate(pipe)); x=r[0] if r else {}
    pct=round_half((x.get('present_days',0)/working*100),2) if working else None
    return {'emp_code':emp_code,'month':month,'working_days':working,'present_days':round_half(x.get('present_days',0)),'leave_days':x.get('leave_days',0),'late_count':x.get('late_count',0),'total_late_minutes':x.get('total_late_minutes',0),'total_overtime_minutes':x.get('total_overtime_minutes',0),'attendance_pct':pct}

@app.get('/analytics/departments/summary')
def department_summary(month:str=Query(...,pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),department:Optional[str]=None):
    start,end=month_range(month)
    pipe=[{'$match':{'joined_on':{'$lte':end},**({'department':department} if department is not None else {})}},{'$lookup':{'from':'attendance_logs','let':{'code':'$emp_code'},'pipeline':[{'$match':{'$expr':{'$and':[{'$eq':['$emp_code','$$code']},{'$gte':['$date',start]},{'$lte':['$date',end]}]}}},{'$project':{'status':1,'date':1,'half_day':1,'work_hours':1,'late_minutes':1,'overtime_minutes':1}}],'as':'logs'}},{'$unwind':{'path':'$logs','preserveNullAndEmptyArrays':True}},{'$group':{'_id':'$department','headcount':{'$addToSet':'$emp_code'},'present_days':{'$sum':{'$cond':[{'$and':[{'$in':['$logs.status',list(PRESENCE)]},{'$in':[{'$dayOfWeek':{'$dateFromString':{'dateString':'$logs.date'}}},[2,3,4,5,6]]}]},{'$cond':[{'$eq':['$logs.half_day',True]},0.5,1]},0]}},'work_sum':{'$sum':{'$cond':[{'$and':[{'$in':['$logs.status',list(PRESENCE)]},{'$ne':[{'$ifNull':['$logs.work_hours',None]},None]}]},'$logs.work_hours',0]}},'work_n':{'$sum':{'$cond':[{'$and':[{'$in':['$logs.status',list(PRESENCE)]},{'$ne':[{'$ifNull':['$logs.work_hours',None]},None]}]},1,0]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$logs.late_minutes',0]},0]},1,0]}},'total_late_minutes':{'$sum':{'$ifNull':['$logs.late_minutes',0]}},'leave_count':{'$sum':{'$cond':[{'$eq':['$logs.status','LEAVE']},1,0]}},'on_duty_count':{'$sum':{'$cond':[{'$eq':['$logs.status','ON_DUTY']},1,0]}}}},{'$project':{'_id':0,'department':'$_id','headcount':{'$size':'$headcount'},'present_days':1,'avg_work_hours':{'$cond':[{'$gt':['$work_n',0]},{'$round':[{'$divide':['$work_sum','$work_n']},2]},None]},'late_count':1,'total_late_minutes':1,'leave_count':1,'on_duty_count':1}},{'$sort':{'department':1}}]
    rows=list(db.employees.aggregate(pipe)); return {'month':month,'items':rows}

@app.get('/analytics/leaderboard/late')
def late_leaderboard(month:str=Query(...,pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),limit:int=Query(10,ge=1,le=50),department:Optional[str]=None):
    start,end=month_range(month)
    pipe=[{'$match':{'date':{'$gte':start,'$lte':end},'late_minutes':{'$gt':0}}},{'$group':{'_id':'$emp_code','total_late_minutes':{'$sum':'$late_minutes'},'late_count':{'$sum':1}}},{'$lookup':{'from':'employees','localField':'_id','foreignField':'emp_code','as':'emp'}},{'$unwind':'$emp'}]
    if department is not None: pipe.append({'$match':{'emp.department':department}})
    pipe += [{'$setWindowFields':{'sortBy':{'total_late_minutes':-1},'output':{'rank':{'$rank':{}}}}},{'$match':{'rank':{'$lte':limit}}},{'$sort':{'total_late_minutes':-1,'_id':1}},{'$project':{'_id':0,'rank':1,'emp_code':'$_id','name':'$emp.name','department':'$emp.department','total_late_minutes':1,'late_count':1}}]
    return {'month':month,'items':list(db.attendance_logs.aggregate(pipe))}

@app.get('/analytics/departments/{department}/trend')
def department_trend(department:str,from_:str=Query(...,alias='from'),to:str=Query(...)):
    a=valid_date(from_); b=valid_date(to)
    if b<a or (b-a).days+1>92: raise HTTPException(422,'invalid date range')
    if not db.employees.find_one({'department':department}): raise HTTPException(404,'department not found')
    # Generate calendar rows in MongoDB; lookup per-day records and compute moving average with a window stage.
    pipe=[{'$match':{'department':department}},{'$group':{'_id':None,'codes':{'$push':'$emp_code'},'joiners':{'$push':{'code':'$emp_code','joined_on':'$joined_on'}}}},{'$project':{'_id':0,'days':{'$map':{'input':{'$range':[0,(b-a).days+1]},'as':'i','in':{'$dateToString':{'date':{'$dateAdd':{'startDate':{'$dateFromString':{'dateString':from_}},'unit':'day','amount':'$$i'}},'format':'%Y-%m-%d','timezone':'UTC'}}}},'codes':1,'joiners':1}},{'$unwind':'$days'},{'$set':{'day':'$days'}},{'$lookup':{'from':'attendance_logs','let':{'d':'$day','codes':'$codes'},'pipeline':[{'$match':{'$expr':{'$and':[{'$eq':['$date','$$d']},{'$in':['$emp_code','$$codes']}]}}},{'$group':{'_id':None,'present_count':{'$sum':{'$cond':[{'$in':['$status',list(PRESENCE)]},{'$cond':[{'$eq':['$half_day',True]},0.5,1]},0]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$late_minutes',0]},0]},1,0]}}}}],'as':'daily'}},{'$set':{'daily':{'$ifNull':[{'$arrayElemAt':['$daily',0]},{'present_count':0,'late_count':0}]},'headcount':{'$size':{'$filter':{'input':'$joiners','as':'j','cond':{'$lte':['$$j.joined_on','$day']}}}},'is_working_day':{'$in':[{'$dayOfWeek':{'$dateFromString':{'dateString':'$day'}}},[2,3,4,5,6]]}}},{'$project':{'_id':0,'date':'$day','is_working_day':1,'headcount':1,'present_count':'$daily.present_count','late_count':'$daily.late_count','attendance_rate':{'$cond':[{'$and':['$is_working_day',{'$gt':['$headcount',0]}]},{'$round':[{'$multiply':[{'$divide':['$daily.present_count','$headcount']},1]},4]},None]}}},{'$setWindowFields':{'sortBy':{'date':1},'output':{'moving_avg_7d':{'$avg':'$attendance_rate','window':{'documents':[-6,0]}}}}},{'$project':{'date':1,'is_working_day':1,'headcount':1,'present_count':1,'late_count':1,'attendance_rate':1,'moving_avg_7d':{'$cond':[{'$eq':[{'$type':'$moving_avg_7d'},'missing']},None,{'$round':['$moving_avg_7d',4]}]}}},{'$sort':{'date':1}}]
    rows=list(db.employees.aggregate(pipe)); return {'department':department,'items':rows}

@app.get('/admin/explain/{endpoint}')
def explain(endpoint:str,emp_code:Optional[str]=None,month:Optional[str]=None,department:Optional[str]=None,limit:int=Query(10,ge=1,le=50),date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[str]=None,from_:Optional[str]=Query(None,alias='from'),to:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    allowed={'attendance_list','employee_monthly','department_summary','late_leaderboard','department_trend'}
    if endpoint not in allowed: raise HTTPException(422,'unknown endpoint')
    if endpoint=='attendance_list':
        q={}
        if emp_code:q['emp_code']=emp_code
        if date_from:q.setdefault('date',{})['$gte']=date_from
        if date_to:q.setdefault('date',{})['$lte']=date_to
        if status:q['status']=status
        cmd={'find':'attendance_logs','filter':q,'sort':{'date':-1,'emp_code':1},'skip':(page-1)*page_size,'limit':page_size}
        coll='attendance_logs'
    elif endpoint=='employee_monthly':
        if not emp_code or not month: raise HTTPException(422,'emp_code and month are required')
        s,e=month_range(month); pipe=[{'$match':{'emp_code':emp_code,'date':{'$gte':s,'$lte':e}}}]; cmd={'aggregate':'attendance_logs','pipeline':pipe,'cursor':{}}; coll='attendance_logs'
    elif endpoint=='department_summary':
        if not month: raise HTTPException(422,'month is required')
        s,e=month_range(month); pipe=[{'$match':{'joined_on':{'$lte':e},**({'department':department} if department else {})}},{'$lookup':{'from':'attendance_logs','let':{'code':'$emp_code'},'pipeline':[{'$match':{'$expr':{'$and':[{'$eq':['$emp_code','$$code']},{'$gte':['$date',s]},{'$lte':['$date',e]}]}}}],'as':'logs'}}]; cmd={'aggregate':'employees','pipeline':pipe,'cursor':{}}; coll='employees'
    elif endpoint=='late_leaderboard':
        if not month: raise HTTPException(422,'month is required')
        s,e=month_range(month); pipe=[{'$match':{'date':{'$gte':s,'$lte':e},'late_minutes':{'$gt':0}}},{'$group':{'_id':'$emp_code','total_late_minutes':{'$sum':'$late_minutes'}}}]; cmd={'aggregate':'attendance_logs','pipeline':pipe,'cursor':{}}; coll='attendance_logs'
    else:
        if not department or not from_ or not to: raise HTTPException(422,'department, from and to are required')
        a=valid_date(from_); b=valid_date(to)
        if b<a or (b-a).days+1>92: raise HTTPException(422,'invalid date range')
        # Explain the indexed attendance lookup used by the trend pipeline.
        cmd={'find':'attendance_logs','filter':{'date':{'$gte':from_,'$lte':to}},'sort':{'date':1}}; coll='attendance_logs'
    try:
        if 'find' in cmd: raw=db.command({'explain':cmd,'verbosity':'executionStats'})
        else: raw=db.command({'explain':cmd,'verbosity':'executionStats'})
        return {'endpoint':endpoint,'collection':coll,'explain':raw}
    except Exception as e: raise HTTPException(422,f'could not explain query: {e}')
