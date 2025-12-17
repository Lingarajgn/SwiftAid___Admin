from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
from flask_pymongo import PyMongo
from bson import ObjectId
import jwt
import datetime
from functools import wraps
import json
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
from io import StringIO

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'supersecret')
app.config['MONGO_URI'] = os.getenv('MONGO_URI', 'mongodb://localhost:27017/SwiftAid')

CORS(app)
mongo = PyMongo(app)

# JSON encoder to handle ObjectId and datetime properly
class JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, ObjectId):
            return str(o)
        if isinstance(o, datetime):
            return o.isoformat() + 'Z'
        return json.JSONEncoder.default(self, o)

app.json_encoder = JSONEncoder

# JWT token required decorator
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        
        if not token:
            return jsonify({'success': False, 'error': 'Token is missing'}), 401
        
        try:
            if token.startswith('Bearer '):
                token = token[7:]
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            current_user = data['username']
        except jwt.ExpiredSignatureError:
            return jsonify({'success': False, 'error': 'Token has expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'success': False, 'error': 'Token is invalid'}), 401
        except Exception as e:
            return jsonify({'success': False, 'error': 'Token verification failed'}), 401
        
        return f(current_user, *args, **kwargs)
    return decorated

# Serve the admin dashboard
@app.route('/')
def serve_dashboard():
    return send_from_directory('.', 'admin_dashboard.html')

# Serve static files
@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('.', path)

# Routes
@app.route('/admin/login', methods=['POST'])
def admin_login():
    try:
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        
        if username == 'admin' and password == 'admin123':
            token = jwt.encode({
                'username': username,
                'exp': datetime.utcnow() + timedelta(hours=24)
            }, app.config['SECRET_KEY'], algorithm='HS256')
            
            return jsonify({
                'success': True,
                'token': token,
                'user': {
                    'username': username,
                    'name': 'Admin User',
                    'role': 'Administrator'
                }
            })
        else:
            return jsonify({'success': False, 'error': 'Invalid credentials'}), 401
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/dashboard/incidents', methods=['GET'])
@token_required
def get_incidents(current_user):
    try:
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 50))
        skip = (page - 1) * limit
        
        incidents_cursor = mongo.db.incidents.find().sort('timestamp', -1).skip(skip).limit(limit)
        incidents = list(incidents_cursor)
        
        processed_incidents = []
        for incident in incidents:
            user_data = mongo.db.users.find_one({'email': incident.get('user_email')})
            
            timestamp = incident.get('timestamp')
            if timestamp and isinstance(timestamp, datetime):
                timestamp_str = timestamp.isoformat() + 'Z'
            else:
                timestamp_str = timestamp
            
            processed_incident = {
                '_id': str(incident.get('_id')),
                'incident_id': incident.get('incident_id'),
                'user_email': incident.get('user_email'),
                'user_name': incident.get('user_name') or (user_data.get('name') if user_data else 'Unknown User'),
                'lat': incident.get('lat'),
                'lng': incident.get('lng'),
                'accel_mag': incident.get('accel_mag'),
                'speed': incident.get('speed', 0),
                'metadata': incident.get('metadata', {}),
                'timestamp': timestamp_str,
                'created_at': timestamp_str,
                'emails_sent': incident.get('emails_sent', 0)
            }
            processed_incidents.append(processed_incident)
        
        return jsonify(processed_incidents)
        
    except Exception as e:
        print(f"Error in get_incidents: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500
    
@app.route('/admin/ambulance-assignments', methods=['GET'])
@token_required
def get_ambulance_assignments(current_user):
    try:
        # Get all ambulances that are assigned to incidents
        assigned_ambulances = list(mongo.db.ambulances.find({
            'current_incident_id': {'$exists': True, '$ne': None}
        }))
        
        # Get incident details for each assigned ambulance
        assignments_with_details = []
        for ambulance in assigned_ambulances:
            incident_id = ambulance.get('current_incident_id')
            incident = None
            
            if incident_id:
                try:
                    incident = mongo.db.incidents.find_one({'_id': ObjectId(incident_id)})
                except:
                    # Try finding by incident_id field if _id search fails
                    incident = mongo.db.incidents.find_one({'incident_id': incident_id})
            
            assignment_data = {
                'ambulance_id': str(ambulance.get('_id')),
                'vehicle_number': ambulance.get('vehicle_number'),
                'driver_name': ambulance.get('driver_name'),
                'phone': ambulance.get('phone'),
                'status': ambulance.get('status'),
                'hospital_name': ambulance.get('hospital_name'),
                'current_incident_id': ambulance.get('current_incident_id'),
                'incident_details': None
            }
            
            if incident:
                assignment_data['incident_details'] = {
                    'incident_id': str(incident.get('_id')),
                    'user_name': incident.get('user_name'),
                    'user_email': incident.get('user_email'),
                    'lat': incident.get('lat'),
                    'lng': incident.get('lng'),
                    'timestamp': incident.get('timestamp')
                }
            
            assignments_with_details.append(assignment_data)
        
        return jsonify({
            'success': True,
            'assignments': assignments_with_details,
            'total_assigned': len(assignments_with_details)
        })
        
    except Exception as e:
        print(f"Error in get_ambulance_assignments: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/ambulances/<ambulance_id>', methods=['GET'])
@token_required
def get_ambulance_details(current_user, ambulance_id):
    try:
        ambulance = mongo.db.ambulances.find_one({'_id': ObjectId(ambulance_id)})
        
        if not ambulance:
            return jsonify({'success': False, 'error': 'Ambulance not found'}), 404
        
        # Get incident details if assigned
        incident_details = None
        if ambulance.get('current_incident_id'):
            incident = mongo.db.incidents.find_one({'_id': ObjectId(ambulance['current_incident_id'])})
            if incident:
                incident_details = {
                    'incident_id': str(incident.get('_id')),
                    'user_name': incident.get('user_name'),
                    'user_email': incident.get('user_email'),
                    'lat': incident.get('lat'),
                    'lng': incident.get('lng'),
                    'timestamp': incident.get('timestamp')
                }
        
        ambulance_data = {
            '_id': str(ambulance.get('_id')),
            'vehicle_number': ambulance.get('vehicle_number'),
            'driver_name': ambulance.get('driver_name'),
            'phone': ambulance.get('phone'),
            'status': ambulance.get('status'),
            'hospital_name': ambulance.get('hospital_name'),
            'current_incident_id': ambulance.get('current_incident_id'),
            'assignment_time': ambulance.get('assignment_time'),
            'incident_details': incident_details
        }
        
        return jsonify(ambulance_data)
        
    except Exception as e:
        print(f"Error in get_ambulance_details: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/ambulances/<ambulance_id>/unassign', methods=['POST'])
@token_required
def unassign_ambulance(current_user, ambulance_id):
    try:
        result = mongo.db.ambulances.update_one(
            {'_id': ObjectId(ambulance_id)},
            {'$set': {
                'current_incident_id': None,
                'assignment_time': None
            }}
        )
        
        if result.modified_count == 1:
            return jsonify({'success': True, 'message': 'Ambulance unassigned successfully'})
        else:
            return jsonify({'success': False, 'error': 'Ambulance not found or already unassigned'}), 404
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/ambulances/<ambulance_id>', methods=['DELETE'])
@token_required
def delete_ambulance(current_user, ambulance_id):
    try:
        result = mongo.db.ambulances.delete_one({'_id': ObjectId(ambulance_id)})
        
        if result.deleted_count == 1:
            return jsonify({'success': True, 'message': 'Ambulance deleted successfully'})
        else:
            return jsonify({'success': False, 'error': 'Ambulance not found'}), 404
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    

@app.route('/dashboard/incidents/<incident_id>', methods=['GET'])
@token_required
def get_incident_details(current_user, incident_id):
    try:
        incident = mongo.db.incidents.find_one({'_id': ObjectId(incident_id)})
        
        if not incident:
            return jsonify({'success': False, 'error': 'Incident not found'}), 404
        
        user_data = mongo.db.users.find_one({'email': incident.get('user_email')})
        
        timestamp = incident.get('timestamp')
        if timestamp and isinstance(timestamp, datetime):
            timestamp_str = timestamp.isoformat() + 'Z'
        else:
            timestamp_str = timestamp
        
        processed_incident = {
            '_id': str(incident.get('_id')),
            'incident_id': incident.get('incident_id'),
            'user_email': incident.get('user_email'),
            'user_name': incident.get('user_name') or (user_data.get('name') if user_data else 'Unknown User'),
            'lat': incident.get('lat'),
            'lng': incident.get('lng'),
            'accel_mag': incident.get('accel_mag'),
            'speed': incident.get('speed', 0),
            'metadata': incident.get('metadata', {}),
            'timestamp': timestamp_str,
            'created_at': timestamp_str,
            'emails_sent': incident.get('emails_sent', 0)
        }
        
        return jsonify(processed_incident)
        
    except Exception as e:
        print(f"Error in get_incident_details: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/dashboard/incidents/<incident_id>', methods=['DELETE'])
@token_required
def delete_incident(current_user, incident_id):
    try:
        result = mongo.db.incidents.delete_one({'_id': ObjectId(incident_id)})
        
        if result.deleted_count == 1:
            return jsonify({'success': True, 'message': 'Incident deleted successfully'})
        else:
            return jsonify({'success': False, 'error': 'Incident not found'}), 404
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/dashboard/incidents/export', methods=['GET'])
@token_required
def export_incidents_csv(current_user):
    try:
        incidents_cursor = mongo.db.incidents.find().sort('timestamp', -1)
        incidents = list(incidents_cursor)
        
        csv_content = "Incident ID,User Name,User Email,Type,Latitude,Longitude,Google Maps Link,Acceleration (m/s²),Speed (km/h),Timestamp,Status\n"
        
        for incident in incidents:
            is_manual = incident.get('metadata', {}).get('manual', False)
            sos_type = incident.get('metadata', {}).get('sos_type', '')
            incident_type = "Manual SOS (Self)" if (is_manual and sos_type == 'self') else \
                           "Manual SOS (Others)" if (is_manual and sos_type == 'other') else \
                           "Auto-detected"
            
            status = "Manual" if is_manual else "Auto"
            
            user_data = mongo.db.users.find_one({'email': incident.get('user_email')})
            user_name = incident.get('user_name') or (user_data.get('name') if user_data else 'Unknown User')
            
            timestamp = incident.get('timestamp')
            if timestamp and isinstance(timestamp, datetime):
                timestamp_str = timestamp.strftime('%Y-%m-%d %H:%M:%S')
            else:
                timestamp_str = str(timestamp)
            
            lat = incident.get('lat')
            lng = incident.get('lng')
            maps_link = f"https://www.google.com/maps?q={lat},{lng}" if lat and lng else "N/A"
            
            csv_content += f'"{incident.get("incident_id", "")}","{user_name}","{incident.get("user_email", "")}",'
            csv_content += f'"{incident_type}",{lat},{lng},'
            csv_content += f'"{maps_link}",{incident.get("accel_mag", "")},{incident.get("speed", "")},'
            csv_content += f'"{timestamp_str}","{status}"\n'
        
        filename = f"incidents_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
        
        return Response(
            csv_content,
            mimetype="text/csv",
            headers={"Content-disposition": f"attachment; filename={filename}"}
        )
        
    except Exception as e:
        print(f"Error in export_incidents_csv: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/users', methods=['GET'])
@token_required
def get_users(current_user):
    try:
        users_cursor = mongo.db.users.find()
        users = list(users_cursor)
        
        users_with_details = []
        for user in users:
            profile = mongo.db.profiles.find_one({'user_email': user.get('email')})
            emergency_contacts = list(mongo.db.contacts.find({'user_email': user.get('email')}))
            user_incidents = list(mongo.db.incidents.find({'user_email': user.get('email')}))
            
            created_at = user.get('created_at')
            if created_at and isinstance(created_at, datetime):
                created_at_str = created_at.isoformat() + 'Z'
            else:
                created_at_str = created_at
            
            last_incident = user_incidents[0].get('timestamp') if user_incidents else None
            if last_incident and isinstance(last_incident, datetime):
                last_incident_str = last_incident.isoformat() + 'Z'
            else:
                last_incident_str = last_incident
            
            user_data = {
                '_id': str(user.get('_id')),
                'name': user.get('name'),
                'email': user.get('email'),
                'username': user.get('username'),
                'created_at': created_at_str,
                'profile': profile,
                'emergency_contacts': emergency_contacts,
                'total_incidents': len(user_incidents),
                'last_incident': last_incident_str
            }
            users_with_details.append(user_data)
        
        return jsonify(users_with_details)
        
    except Exception as e:
        print(f"Error in get_users: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/users/<user_id>', methods=['GET'])
@token_required
def get_user_details(current_user, user_id):
    try:
        user = mongo.db.users.find_one({'_id': ObjectId(user_id)})
        
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404
        
        profile = mongo.db.profiles.find_one({'user_email': user.get('email')})
        emergency_contacts = list(mongo.db.contacts.find({'user_email': user.get('email')}))
        user_incidents = list(mongo.db.incidents.find({'user_email': user.get('email')}).sort('timestamp', -1).limit(10))
        
        created_at = user.get('created_at')
        if created_at and isinstance(created_at, datetime):
            created_at_str = created_at.isoformat() + 'Z'
        else:
            created_at_str = created_at
        
        last_incident = user_incidents[0].get('timestamp') if user_incidents else None
        if last_incident and isinstance(last_incident, datetime):
            last_incident_str = last_incident.isoformat() + 'Z'
        else:
            last_incident_str = last_incident
        
        user_data = {
            '_id': str(user.get('_id')),
            'name': user.get('name'),
            'email': user.get('email'),
            'username': user.get('username'),
            'created_at': created_at_str,
            'profile': profile,
            'emergency_contacts': emergency_contacts,
            'total_incidents': len(user_incidents),
            'recent_incidents': user_incidents,
            'last_incident': last_incident_str
        }
        
        return jsonify(user_data)
        
    except Exception as e:
        print(f"Error in get_user_details: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/users/<user_id>', methods=['DELETE'])
@token_required
def delete_user(current_user, user_id):
    try:
        user = mongo.db.users.find_one({'_id': ObjectId(user_id)})
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404
        
        user_email = user.get('email')
        
        result = mongo.db.users.delete_one({'_id': ObjectId(user_id)})
        
        if result.deleted_count == 1:
            mongo.db.profiles.delete_one({'user_email': user_email})
            mongo.db.contacts.delete_many({'user_email': user_email})
            mongo.db.incidents.delete_many({'user_email': user_email})
            
            return jsonify({'success': True, 'message': 'User and all related data deleted successfully'})
        else:
            return jsonify({'success': False, 'error': 'User not found'}), 404
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# HOSPITALS ROUTES
@app.route('/admin/hospitals', methods=['GET'])
@token_required
def get_hospitals(current_user):
    try:
        print("Attempting to fetch hospitals from hospital_user collection...")
        
        hospitals_cursor = mongo.db.hospital_user.find()
        hospitals_data = list(hospitals_cursor)
        
        print(f"Found {len(hospitals_data)} hospitals in hospital_user collection")
        
        processed_hospitals = []
        for hospital in hospitals_data:
            hospital_data = {
                '_id': str(hospital.get('_id')),
                'hospital_name': hospital.get('hospital_name'),
                'email': hospital.get('email'),
                'phone': hospital.get('phone'),
                'location': hospital.get('location')
            }
            processed_hospitals.append(hospital_data)
            print(f"Processed hospital: {hospital_data['hospital_name']}")
        
        print(f"Returning {len(processed_hospitals)} hospitals")
        return jsonify(processed_hospitals)
        
    except Exception as e:
        print(f"Error in get_hospitals: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/hospitals/<hospital_id>', methods=['GET'])
@token_required
def get_hospital_details(current_user, hospital_id):
    try:
        print(f"Fetching hospital details for ID: {hospital_id} from hospital_user collection")
        
        hospital = mongo.db.hospital_user.find_one({'_id': ObjectId(hospital_id)})
        
        if not hospital:
            print(f"Hospital not found with ID: {hospital_id}")
            return jsonify({'success': False, 'error': 'Hospital not found'}), 404
        
        hospital_data = {
            '_id': str(hospital.get('_id')),
            'hospital_name': hospital.get('hospital_name'),
            'email': hospital.get('email'),
            'phone': hospital.get('phone'),
            'location': hospital.get('location')
        }
        
        return jsonify(hospital_data)
        
    except Exception as e:
        print(f"Error in get_hospital_details: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/hospitals/<hospital_id>', methods=['DELETE'])
@token_required
def delete_hospital(current_user, hospital_id):
    try:
        print(f"Attempting to delete hospital: {hospital_id} from hospital_user collection")
        
        result = mongo.db.hospital_user.delete_one({'_id': ObjectId(hospital_id)})
        
        if result.deleted_count == 1:
            return jsonify({'success': True, 'message': 'Hospital deleted successfully'})
        else:
            return jsonify({'success': False, 'error': 'Hospital not found'}), 404
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# INCIDENT TRACKING ROUTES - FIXED VERSION
@app.route('/admin/incident-hospitals/<incident_id>', methods=['GET'])
@token_required
def get_incident_hospitals(current_user, incident_id):
    try:
        incident = mongo.db.incidents.find_one({'_id': ObjectId(incident_id)})
        if not incident:
            return jsonify({'success': False, 'error': 'Incident not found'}), 404

        hospitals = list(mongo.db.hospital_user.find())
        
        incident_assignments = []
        if 'incident_assignments' in mongo.db.list_collection_names():
            incident_assignments = list(mongo.db.incident_assignments.find({
                'incident_id': str(incident_id)
            }))
        
        ambulance_assignments = {}
        for assignment in incident_assignments:
            ambulance = mongo.db.ambulances.find_one({
                'hospital_name': assignment['hospital_name'],
                'status': 'on-duty'
            })
            if ambulance:
                ambulance_assignments[assignment['hospital_name']] = ambulance

        result = {
            'incident': {
                '_id': str(incident['_id']),
                'incident_id': incident.get('incident_id'),
                'user_email': incident.get('user_email'),
                'user_name': incident.get('user_name'),
                'timestamp': incident.get('timestamp')
            },
            'nearby_hospitals': [],
            'accepted_hospitals': [],
            'ambulance_assignments': ambulance_assignments
        }

        for hospital in hospitals:
            hospital_data = {
                '_id': str(hospital['_id']),
                'hospital_name': hospital.get('hospital_name'),
                'email': hospital.get('email'),
                'phone': hospital.get('phone'),
                'location': hospital.get('location'),
                'distance': '5 km'
            }
            result['nearby_hospitals'].append(hospital_data)

        for assignment in incident_assignments:
            hospital = mongo.db.hospital_user.find_one({
                'hospital_name': assignment['hospital_name']
            })
            if hospital:
                hospital_data = {
                    '_id': str(hospital['_id']),
                    'hospital_name': hospital.get('hospital_name'),
                    'email': hospital.get('email'),
                    'phone': hospital.get('phone'),
                    'location': hospital.get('location'),
                    'accepted_at': assignment.get('accepted_at'),
                    'status': assignment.get('status', 'pending')
                }
                result['accepted_hospitals'].append(hospital_data)

        return jsonify(result)

    except Exception as e:
        print(f"Error in get_incident_hospitals: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/incident-assignments', methods=['GET'])
@token_required
def get_all_incident_assignments(current_user):
    try:
        # Get all incidents
        incidents = list(mongo.db.incidents.find().sort('timestamp', -1))
        
        processed_incidents = []
        
        for incident in incidents:
            incident_id = str(incident['_id'])
            
            # Get hospital assignments for this incident
            hospital_assignments = []
            if 'incident_assignments' in mongo.db.list_collection_names():
                hospital_assignments = list(mongo.db.incident_assignments.find({
                    'incident_id': incident_id
                }))
            
            # Get ambulance assignments for this incident
            ambulance_assignments = []
            if 'ambulances' in mongo.db.list_collection_names():
                ambulance_assignments = list(mongo.db.ambulances.find({
                    'assigned_incident_id': incident_id
                }))
            
            # Count statistics - FIXED LOGIC
            total_notified = len(hospital_assignments)
            total_accepted = len([a for a in hospital_assignments if a.get('status') == 'accepted'])
            ambulances_assigned = len(ambulance_assignments)
            
            incident_data = {
                '_id': incident_id,
                'incident_id': incident.get('incident_id'),
                'user_name': incident.get('user_name'),
                'user_email': incident.get('user_email'),
                'timestamp': incident.get('timestamp'),
                'hospital_assignments': hospital_assignments,
                'ambulance_assignments': ambulance_assignments,
                'total_hospitals_notified': total_notified,
                'hospitals_accepted': total_accepted,
                'ambulances_assigned': ambulances_assigned
            }
            processed_incidents.append(incident_data)
        
        return jsonify(processed_incidents)
        
    except Exception as e:
        print(f"Error in get_all_incident_assignments: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# CREATE TEST ASSIGNMENTS ENDPOINT
@app.route('/admin/create-test-assignments', methods=['POST'])
@token_required
def create_test_assignments(current_user):
    try:
        # Get recent incidents
        incidents = list(mongo.db.incidents.find().sort('timestamp', -1).limit(5))
        
        # Get hospitals
        hospitals = list(mongo.db.hospital_user.find())
        
        assignments_created = 0
        
        for incident in incidents:
            incident_id = str(incident['_id'])
            
            # Assign 2 hospitals to each incident (like in your screenshot)
            for i, hospital in enumerate(hospitals[:2]):  # First 2 hospitals
                assignment = {
                    'incident_id': incident_id,
                    'hospital_id': str(hospital['_id']),
                    'hospital_name': hospital['hospital_name'],
                    'status': 'accepted' if i == 0 else 'notified',  # First hospital accepted, second notified
                    'assigned_at': datetime.utcnow(),
                    'accepted_at': datetime.utcnow() if i == 0 else None
                }
                
                # Insert into incident_assignments collection
                mongo.db.incident_assignments.insert_one(assignment)
                assignments_created += 1
                
                # Also assign ambulance if hospital accepted
                if i == 0:  # For the accepted hospital
                    ambulance = mongo.db.ambulances.find_one({
                        'hospital_name': hospital['hospital_name']
                    })
                    if ambulance:
                        # Update ambulance assignment
                        mongo.db.ambulances.update_one(
                            {'_id': ambulance['_id']},
                            {'$set': {
                                'assigned_incident_id': incident_id,
                                'assignment_time': datetime.utcnow()
                            }}
                        )
        
        return jsonify({
            'success': True, 
            'message': f'Created {assignments_created} test assignments',
            'assignments_created': assignments_created
        })
        
    except Exception as e:
        print(f"Error creating test assignments: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/dashboard/stats', methods=['GET'])
@token_required
def get_dashboard_stats(current_user):
    try:
        total_users = mongo.db.users.count_documents({})
        total_incidents = mongo.db.incidents.count_documents({})
        
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_incidents = mongo.db.incidents.count_documents({
            'timestamp': {'$gte': today_start}
        })
        
        total_hospitals = mongo.db.hospital_user.count_documents({})
        total_police = mongo.db.POLICE_users.count_documents({})
        
        active_assignments = 0
        if 'incident_assignments' in mongo.db.list_collection_names():
            active_assignments = mongo.db.incident_assignments.count_documents({
                'status': 'accepted'
            })
        
        total_contacts = mongo.db.contacts.count_documents({})
        emails_sent = total_incidents * total_contacts
        
        manual_self_incidents = mongo.db.incidents.count_documents({
            'metadata.manual': True,
            'metadata.sos_type': 'self'
        })
        
        manual_other_incidents = mongo.db.incidents.count_documents({
            'metadata.manual': True,
            'metadata.sos_type': 'other'
        })
        
        auto_incidents = mongo.db.incidents.count_documents({
            'metadata.manual': False
        })
        
        stats = {
            'total_users': total_users,
            'total_incidents': total_incidents,
            'today_incidents': today_incidents,
            'total_hospitals': total_hospitals,
            'total_police': total_police,
            'active_assignments': active_assignments,
            'emails_sent': emails_sent,
            'incident_types': {
                'manual_self': manual_self_incidents,
                'manual_other': manual_other_incidents,
                'auto_detected': auto_incidents
            }
        }
        
        return jsonify(stats)
        
    except Exception as e:
        print(f"Error in get_dashboard_stats: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/dashboard/analytics/trends', methods=['GET'])
@token_required
def get_incident_trends(current_user):
    try:
        days = int(request.args.get('days', 30))
        
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days)
        
        date_range = []
        current_date = start_date
        while current_date <= end_date:
            date_range.append(current_date.date())
            current_date += timedelta(days=1)
        
        daily_counts = []
        for single_date in date_range:
            next_date = single_date + timedelta(days=1)
            day_count = mongo.db.incidents.count_documents({
                'timestamp': {
                    '$gte': datetime.combine(single_date, datetime.min.time()),
                    '$lt': datetime.combine(next_date, datetime.min.time())
                }
            })
            daily_counts.append({
                'date': single_date.strftime('%Y-%m-%d'),
                'count': day_count
            })
        
        return jsonify(daily_counts)
        
    except Exception as e:
        print(f"Error in get_incident_trends: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/dashboard/analytics/hourly', methods=['GET'])
@token_required
def get_hourly_distribution(current_user):
    try:
        pipeline = [
            {
                '$group': {
                    '_id': {'$hour': '$timestamp'},
                    'count': {'$sum': 1}
                }
            },
            {
                '$sort': {'_id': 1}
            }
        ]
        
        hourly_cursor = mongo.db.incidents.aggregate(pipeline)
        hourly_data = list(hourly_cursor)
        
        hourly_distribution = [0] * 24
        for data in hourly_data:
            hour = data['_id']
            hourly_distribution[hour] = data['count']
        
        return jsonify(hourly_distribution)
        
    except Exception as e:
        print(f"Error in get_hourly_distribution: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/contacts', methods=['GET'])
@token_required
def get_emergency_contacts(current_user):
    try:
        contacts_cursor = mongo.db.contacts.find()
        contacts = list(contacts_cursor)
        
        return jsonify(contacts)
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/debug/hospitals', methods=['GET'])
def debug_hospitals():
    try:
        collections = mongo.db.list_collection_names()
        
        hospitals_data = {}
        for collection_name in ['hospitals', 'hospital', 'Hospitals', 'Hospital']:
            try:
                if collection_name in collections:
                    data = list(mongo.db[collection_name].find())
                    hospitals_data[collection_name] = {
                        'count': len(data),
                        'sample': data[:2] if data else []
                    }
            except Exception as e:
                hospitals_data[collection_name] = {'error': str(e)}
        
        return jsonify({
            'all_collections': collections,
            'hospitals_data': hospitals_data
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    try:
        mongo.db.command('ping')
        
        return jsonify({
            'status': 'healthy',
            'database': 'connected',
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'service': 'SwiftAid Backend API'
        })
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'database': 'disconnected',
            'error': str(e),
            'timestamp': datetime.utcnow().isoformat() + 'Z'
        }), 500

# POLICE STATIONS ROUTES - UPDATED FOR POLICE_USERS COLLECTION
@app.route('/admin/police-stations', methods=['GET'])
@token_required
def get_police_stations(current_user):
    try:
        print("Fetching police officers from POLICE_users collection...")
        
        # Get all police officers
        police_cursor = mongo.db.POLICE_users.find()
        police_data = list(police_cursor)
        
        print(f"Found {len(police_data)} police officers")
        
        processed_police = []
        for officer in police_data:
            officer_data = {
                '_id': str(officer.get('_id')),
                'username': officer.get('username'),
                'email': officer.get('email'),
                'full_name': officer.get('full_name'),
                'police_station': officer.get('police_station'),
                'designation': officer.get('designation'),
                'role': officer.get('role'),
                'status': officer.get('status', 'active'),
                'created_at': officer.get('created_at'),
                'last_login': officer.get('last_login')
            }
            processed_police.append(officer_data)
        
        return jsonify(processed_police)
        
    except Exception as e:
        print(f"Error in get_police_stations: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/police-stations/<officer_id>', methods=['GET'])
@token_required
def get_police_station_details(current_user, officer_id):
    try:
        print(f"Fetching police officer details for ID: {officer_id}")
        
        officer = mongo.db.POLICE_users.find_one({'_id': ObjectId(officer_id)})
        
        if not officer:
            return jsonify({'success': False, 'error': 'Police officer not found'}), 404
        
        officer_data = {
            '_id': str(officer.get('_id')),
            'username': officer.get('username'),
            'email': officer.get('email'),
            'full_name': officer.get('full_name'),
            'police_station': officer.get('police_station'),
            'designation': officer.get('designation'),
            'role': officer.get('role'),
            'status': officer.get('status', 'active'),
            'created_at': officer.get('created_at'),
            'last_login': officer.get('last_login')
        }
        
        return jsonify(officer_data)
        
    except Exception as e:
        print(f"Error in get_police_station_details: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/police-stations/<officer_id>', methods=['DELETE'])
@token_required
def delete_police_station(current_user, officer_id):
    try:
        result = mongo.db.POLICE_users.delete_one({'_id': ObjectId(officer_id)})
        
        if result.deleted_count == 1:
            return jsonify({'success': True, 'message': 'Police officer deleted successfully'})
        else:
            return jsonify({'success': False, 'error': 'Police officer not found'}), 404
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/test', methods=['GET'])
def test_endpoint():
    return jsonify({'message': 'Backend is working!', 'timestamp': datetime.utcnow().isoformat() + 'Z'})

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'success': False, 'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'success': False, 'error': 'Internal server error'}), 500

@app.errorhandler(401)
def unauthorized(error):
    return jsonify({'success': False, 'error': 'Unauthorized access'}), 401

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)